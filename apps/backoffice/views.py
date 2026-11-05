"""Point d'entrée des métriques d'exploitation, au format texte de Prometheus.

Protégé par un jeton porté en en-tête `Authorization: Bearer <jeton>`. Sans
jeton configuré, le point d'entrée refuse tout.
"""

import hmac
import logging
import time

from django.conf import settings
from django.http import HttpResponse

from . import selectors

journal = logging.getLogger(__name__)

ETATS_LIAISON = ("CONNECTE", "DEGRADE", "DECONNECTE")


def _etiquette(valeur: str) -> str:
    return str(valeur).replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\n")


def _autorise(request) -> bool:
    attendu = settings.METRIQUES_JETON
    fourni = request.headers.get("Authorization", "")
    if not attendu or not fourni.startswith("Bearer "):
        return False
    return hmac.compare_digest(fourni.removeprefix("Bearer ").encode(), attendu.encode())


def _lignes() -> list[str]:
    lignes = []
    files = selectors.profondeur_files()
    lignes += [
        "# HELP sms_courtier_joignable 1 si l'API de gestion de RabbitMQ répond.",
        "# TYPE sms_courtier_joignable gauge",
        f"sms_courtier_joignable {0 if files is None else 1}",
        "# HELP sms_file_profondeur Messages en attente dans la file.",
        "# TYPE sms_file_profondeur gauge",
    ]
    for nom, profondeur in sorted((files or {}).items()):
        lignes.append(f'sms_file_profondeur{{file="{_etiquette(nom)}"}} {profondeur}')

    liaisons, publiees_le = selectors.etat_liaisons()
    age = int(time.time()) - publiees_le if publiees_le else -1
    lignes += [
        "# HELP sms_liaisons_age_secondes Âge de l'état des liaisons publié par"
        " l'ordonnanceur, -1 si rien n'est publié.",
        "# TYPE sms_liaisons_age_secondes gauge",
        f"sms_liaisons_age_secondes {age}",
    ]
    lignes += [
        "# HELP sms_liaison_etat 1 pour l'état courant de la liaison, 0 sinon.",
        "# TYPE sms_liaison_etat gauge",
    ]
    for liaison in liaisons:
        base = f'connecteur="{_etiquette(liaison.nom)}",operateur="{liaison.operateur}"'
        for etat in ETATS_LIAISON:
            valeur = 1 if liaison.etat == etat else 0
            lignes.append(f'sms_liaison_etat{{{base},etat="{etat}"}} {valeur}')

    soumis = selectors.messages_soumis([liaison.connecteur_id for liaison in liaisons])
    lignes += [
        "# HELP sms_messages_soumis_total Messages soumis à la passerelle, par connecteur.",
        "# TYPE sms_messages_soumis_total counter",
    ]
    for liaison in liaisons:
        total = soumis.get(liaison.connecteur_id, 0)
        lignes.append(
            f'sms_messages_soumis_total{{connecteur="{_etiquette(liaison.nom)}"}} {total}'
        )
    lignes += [
        "# HELP sms_desinscriptions_sans_emetteur_total STOP reçus sans envoi"
        " correspondant dans les 72 heures, gardés en base.",
        "# TYPE sms_desinscriptions_sans_emetteur_total counter",
        f"sms_desinscriptions_sans_emetteur_total {selectors.desinscriptions_sans_emetteur()}",
    ]
    return lignes


def metriques(request):
    if not _autorise(request):
        journal.warning("Accès refusé au point d'entrée des métriques.")
        return HttpResponse("Jeton requis.\n", status=401, content_type="text/plain")
    journal.info("Métriques servies.")
    return HttpResponse(
        "\n".join(_lignes()) + "\n", content_type="text/plain; version=0.0.4; charset=utf-8"
    )
