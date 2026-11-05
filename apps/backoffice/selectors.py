"""Lectures des métriques d'exploitation : débit d'envoi, files, liaisons."""

import base64
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass

from django.conf import settings

from apps.core.redis import client_redis
from apps.journal.services import CLE_DESINSCRIPTIONS_SANS_EMETTEUR, CLE_SOUMIS
from apps.telecom.supervision import etat_publie

journal = logging.getLogger(__name__)


@dataclass(frozen=True)
class Liaison:
    connecteur_id: str
    nom: str
    operateur: str
    etat: str
    actif: bool


def profondeur_files() -> dict[str, int] | None:
    """Messages en attente par file, lus sur l'API de gestion de RabbitMQ.
    None si le courtier ne répond pas."""
    hote = urllib.parse.quote(settings.RABBITMQ_VHOST, safe="")
    requete = urllib.request.Request(f"{settings.RABBITMQ_API_URL}/queues/{hote}")
    authentification = f"{settings.RABBITMQ_USER}:{settings.RABBITMQ_PASSWORD}".encode()
    requete.add_header("Authorization", "Basic " + base64.b64encode(authentification).decode())
    try:
        with urllib.request.urlopen(requete, timeout=3) as reponse:
            files = json.load(reponse)
    except OSError:
        journal.warning("API de gestion de RabbitMQ injoignable.")
        return None
    return {file["name"]: int(file.get("messages", 0)) for file in files}


def etat_liaisons() -> tuple[list[Liaison], int | None]:
    """État des connecteurs tel que l'ordonnanceur le publie dans Redis, et
    l'instant de cette publication. Le service web ne lit jamais la table des
    connecteurs, qui porte les secrets des liaisons."""
    publiees, publiees_le = etat_publie()
    liaisons = [
        Liaison(identifiant, v["nom"], v["operateur"], v["etat"], v["actif"])
        for identifiant, v in sorted(publiees.items(), key=lambda paire: paire[1]["nom"])
    ]
    return liaisons, publiees_le


def messages_soumis(connecteurs: list[str]) -> dict[str, int]:
    if not connecteurs:
        return {}
    cles = [CLE_SOUMIS.format(connecteur_id=identifiant) for identifiant in connecteurs]
    valeurs = client_redis().mget(cles)
    return {
        identifiant: int(valeur or 0)
        for identifiant, valeur in zip(connecteurs, valeurs, strict=True)
    }


def desinscriptions_sans_emetteur() -> int:
    """STOP reçus qu'aucun envoi des soixante-douze heures n'explique."""
    return int(client_redis().get(CLE_DESINSCRIPTIONS_SANS_EMETTEUR) or 0)
