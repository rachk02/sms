"""Journalisation structurée.

Chaque ligne porte l'identifiant de la requête, le client et l'utilisateur, et,
quand ils sont connus, le message, la campagne et le connecteur : c'est ce qui
permet de suivre un message de son acceptation à son accusé quand un client
appelle avec un numéro et une heure. Le contexte vit dans des variables de
contexte, propres à chaque requête et à chaque tâche asynchrone.
"""

import contextvars
import json
import logging
import re
import uuid
from contextlib import contextmanager

CHAMPS = ("requete_id", "client_id", "utilisateur_id", "message_id", "campagne_id", "connecteur_id")

_contexte: dict[str, contextvars.ContextVar] = {
    champ: contextvars.ContextVar(champ, default=None) for champ in CHAMPS
}

ENTETE_REQUETE = "X-Request-ID"
_IDENTIFIANT_VALIDE = re.compile(r"^[A-Za-z0-9._-]{8,64}$")


def poser(**valeurs) -> dict[str, contextvars.Token]:
    return {champ: _contexte[champ].set(valeur) for champ, valeur in valeurs.items()}


def retirer(jetons: dict[str, contextvars.Token]) -> None:
    for champ, jeton in jetons.items():
        _contexte[champ].reset(jeton)


def lire(champ: str):
    """La valeur courante d'un champ du contexte, None hors contexte."""
    return _contexte[champ].get()


@contextmanager
def contexte(**valeurs):
    jetons = poser(**valeurs)
    try:
        yield
    finally:
        retirer(jetons)


def identifiant_requete(propose: str | None) -> str:
    """Reprend l'identifiant fourni par le frontal s'il est sain, sinon en crée un."""
    if propose and _IDENTIFIANT_VALIDE.match(propose):
        return propose
    return uuid.uuid4().hex


class FiltreContexte(logging.Filter):
    def filter(self, enregistrement: logging.LogRecord) -> bool:
        for champ, variable in _contexte.items():
            if getattr(enregistrement, champ, None) is None:
                valeur = variable.get()
                setattr(enregistrement, champ, str(valeur) if valeur is not None else None)
        return True


class FormatJson(logging.Formatter):
    """Une ligne, un objet JSON."""

    def format(self, enregistrement: logging.LogRecord) -> str:
        ligne = {
            "horodatage": self.formatTime(enregistrement, "%Y-%m-%dT%H:%M:%S%z"),
            "niveau": enregistrement.levelname,
            "journal": enregistrement.name,
            "message": enregistrement.getMessage(),
        }
        for champ in CHAMPS:
            valeur = getattr(enregistrement, champ, None)
            if valeur is not None:
                ligne[champ] = valeur
        if enregistrement.exc_info:
            ligne["exception"] = self.formatException(enregistrement.exc_info)
        return json.dumps(ligne, ensure_ascii=False)
