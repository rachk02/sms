"""Lectures des métriques d'exploitation : débit d'envoi, files, liaisons."""

import base64
import json
import logging
import urllib.request
from dataclasses import dataclass

from django.conf import settings

from apps.core.redis import client_redis
from apps.journal.services import CLE_SOUMIS
from apps.telecom.models import Connecteur

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
    requete = urllib.request.Request(f"{settings.RABBITMQ_API_URL}/queues/%2F")
    authentification = f"{settings.RABBITMQ_USER}:{settings.RABBITMQ_PASSWORD}".encode()
    requete.add_header("Authorization", "Basic " + base64.b64encode(authentification).decode())
    try:
        with urllib.request.urlopen(requete, timeout=3) as reponse:
            files = json.load(reponse)
    except OSError:
        journal.warning("API de gestion de RabbitMQ injoignable.")
        return None
    return {file["name"]: int(file.get("messages", 0)) for file in files}


def etat_liaisons() -> list[Liaison]:
    """État de chaque connecteur. Lu par la connexion worker : le rôle
    applicatif ne voit pas la table des connecteurs, qui porte les secrets.
    Seuls des champs sans secret sont lus."""
    return [
        Liaison(
            str(ligne["id"]),
            ligne["nom"],
            ligne["operateur"],
            ligne["etat_liaison"],
            ligne["actif"],
        )
        for ligne in Connecteur.objects.using("worker")
        .order_by("nom")
        .values("id", "nom", "operateur", "etat_liaison", "actif")
    ]


def messages_soumis(connecteurs: list[str]) -> dict[str, int]:
    if not connecteurs:
        return {}
    cles = [CLE_SOUMIS.format(connecteur_id=identifiant) for identifiant in connecteurs]
    valeurs = client_redis().mget(cles)
    return {
        identifiant: int(valeur or 0)
        for identifiant, valeur in zip(connecteurs, valeurs, strict=True)
    }
