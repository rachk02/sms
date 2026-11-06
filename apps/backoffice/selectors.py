"""Lectures du back-office : métriques d'exploitation (débit d'envoi, files,
liaisons) et dossier de preuve d'un numéro."""

import base64
import json
import logging
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db.models import Count, Max, Min
from django.utils import timezone

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


# Dossier de preuve d'un numéro (T-75)

# Le journal se lit sur douze mois, la borne des recherches du journal : la
# durée de rétention n'est pas encore tranchée (décision du 6 novembre 2026).
PERIODE_PREUVE = timedelta(days=365)
# Au-delà, la liste des messages d'un client s'arrête et l'écran le dit.
MESSAGES_AFFICHES = 500


@dataclass(frozen=True)
class PreuveClient:
    client: object
    contact: object | None
    liste_noire: object | None
    messages: int
    premier: datetime | None
    dernier: datetime | None


@dataclass(frozen=True)
class Dossier:
    numero: str
    depuis: datetime
    clients: list
    desinscriptions: list


def _formes_brutes(numero: str) -> list[str]:
    """Les formes sous lesquelles la passerelle rend l'expéditeur d'un SMS
    entrant, qui n'est pas normalisé à l'écriture."""
    national = numero.removeprefix("+")
    return [numero, national, f"00{national}"]


def dossier(numero_normalise: str, maintenant: datetime | None = None) -> Dossier:
    """Pour un numéro, chaque client qui le connaît ou lui a écrit : l'origine
    et la date d'entrée du contact, le consentement déclaré, l'inscription en
    liste noire et sa preuve, le nombre de messages reçus sur douze mois ; puis
    les SMS de désinscription reçus de ce numéro. Lu par le rôle du back-office,
    qui voit tous les clients.

    Le comptage des messages se fait à l'affichage, sur la partition des douze
    derniers mois et l'index du numéro : exception à l'interdit des compteurs
    calculés à la volée, autorisée pour ce seul écran d'agent."""
    from apps.comptes.models import Client
    from apps.contacts.models import Contact, ListeNoire
    from apps.journal.models import Message
    from apps.telecom.models import MessageEntrant

    depuis = (maintenant or timezone.now()) - PERIODE_PREUVE
    contacts = {c.client_id: c for c in Contact.objects.filter(numero_normalise=numero_normalise)}
    inscriptions = {
        e.client_id: e for e in ListeNoire.objects.filter(numero_normalise=numero_normalise)
    }
    envois = {
        ligne["client_id"]: ligne
        for ligne in Message.objects.filter(
            numero_normalise=numero_normalise,
            nature=Message.Nature.CLIENT,
            date_reception__gte=depuis,
            # Reçu : parti vers le réseau. Un message refusé avant le réseau
            # (liste noire, expéditeur) figure dans la liste, pas dans le compte.
            date_soumission__isnull=False,
        )
        .order_by()
        .values("client_id")
        .annotate(n=Count("id"), premier=Min("date_reception"), dernier=Max("date_reception"))
    }
    identifiants = {i for i in (*contacts, *inscriptions, *envois) if i is not None}
    clients = Client.objects.in_bulk(identifiants)
    lignes = [
        PreuveClient(
            client=clients[i],
            contact=contacts.get(i),
            liste_noire=inscriptions.get(i),
            messages=envois.get(i, {}).get("n", 0),
            premier=envois.get(i, {}).get("premier"),
            dernier=envois.get(i, {}).get("dernier"),
        )
        for i in identifiants
    ]
    lignes.sort(key=lambda ligne: ligne.client.raison_sociale)
    if None in inscriptions:
        lignes.insert(
            0, PreuveClient(None, None, inscriptions[None], 0, None, None)
        )  # liste noire de la plateforme
    desinscriptions = list(
        MessageEntrant.objects.filter(
            numero_expediteur__in=_formes_brutes(numero_normalise), recu_le__gte=depuis
        )
        .exclude(mot_cle_reconnu="")
        .order_by("-recu_le")
    )
    return Dossier(numero_normalise, depuis, lignes, desinscriptions)


def messages_recus(numero_normalise: str, client_id, maintenant: datetime | None = None) -> list:
    """Les messages qu'un client a adressés à ce numéro sur douze mois, du plus
    récent au plus ancien, confirmations de désinscription comprises."""
    from apps.journal.models import Message

    depuis = (maintenant or timezone.now()) - PERIODE_PREUVE
    return list(
        Message.objects.filter(
            numero_normalise=numero_normalise, client_id=client_id, date_reception__gte=depuis
        ).order_by("-date_reception")[:MESSAGES_AFFICHES]
    )
