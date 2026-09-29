"""Jeton des adresses de rappel.

Jasmin ne signe pas ses rappels. L'adresse qu'on lui donne porte donc un jeton,
condensat HMAC de l'identifiant du connecteur par un secret que seule la
plateforme connaît : un rappel sans le bon jeton est refusé. Le point d'entrée
n'est de plus joignable que depuis le réseau interne.
"""

import hashlib
import hmac

from django.conf import settings


def jeton(connecteur_id) -> str:
    if not settings.RAPPELS_SECRET:
        raise RuntimeError("RAPPELS_SECRET absent : aucune adresse de rappel ne peut être signée.")
    return hmac.new(
        settings.RAPPELS_SECRET.encode(), str(connecteur_id).encode(), hashlib.sha256
    ).hexdigest()[:32]


def jeton_valide(connecteur_id, propose: str) -> bool:
    if not settings.RAPPELS_SECRET or not propose:
        return False
    return hmac.compare_digest(jeton(connecteur_id), propose)
