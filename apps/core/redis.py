"""Client Redis partagé. Redis est un cache : il ne détient rien d'unique.

Connexion et lecture sont bornées (`REDIS_DELAI_S`, deux secondes par défaut) :
un Redis qui ne répond plus lève une erreur au lieu de bloquer la requête, ce
qui laisse aux quotas le temps de passer sur leurs compteurs de repli
(décision A1 du 9 novembre 2026). Aucune commande bloquante n'est employée.
"""

import logging
from functools import cache

import redis
from django.conf import settings

journal = logging.getLogger(__name__)


@cache
def client_redis() -> redis.Redis:
    delai = settings.REDIS_DELAI_S
    return redis.Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=delai, socket_timeout=delai
    )


def tolerer(operation, description: str) -> bool:
    """Exécute une écriture Redis qui n'est qu'un accélérateur ou une mesure,
    après la validation d'une transaction : si Redis ne répond pas, l'erreur est
    journalisée et la suite continue. Rend vrai si l'écriture a eu lieu. Sans
    cela, la panne remonterait après la validation et ferait rejouer un travail
    déjà fait (correctif du 26 novembre 2026, corrélation des accusés)."""
    try:
        operation()
    except redis.RedisError as erreur:
        journal.error("Redis indisponible, écriture omise (%s) : %s", description, erreur)
        return False
    return True
