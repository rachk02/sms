"""Chiffrement applicatif des secrets conservés en base.

Pour les rares valeurs que la plateforme doit relire en clair (secret du second
facteur, plus tard les identifiants des connecteurs). La clé vit dans
l'environnement, jamais en base ni dans le dépôt : une copie de la base seule
ne livre aucun secret.
"""

from functools import cache

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


class DechiffrementImpossible(Exception):
    pass


@cache
def _fernet() -> Fernet:
    if not settings.SMS_CLE_CHIFFREMENT:
        raise ImproperlyConfigured(
            "SMS_CLE_CHIFFREMENT absente : aucun secret ne peut être chiffré."
        )
    return Fernet(settings.SMS_CLE_CHIFFREMENT.encode())


def chiffrer(clair: str) -> str:
    return _fernet().encrypt(clair.encode()).decode()


def dechiffrer(chiffre: str) -> str:
    try:
        return _fernet().decrypt(chiffre.encode()).decode()
    except InvalidToken:
        raise DechiffrementImpossible("Secret illisible avec la clé en service.") from None
