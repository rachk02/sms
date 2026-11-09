"""Client Redis partagé. Redis est un cache : il ne détient rien d'unique.

Connexion et lecture sont bornées (`REDIS_DELAI_S`, deux secondes par défaut) :
un Redis qui ne répond plus lève une erreur au lieu de bloquer la requête, ce
qui laisse aux quotas le temps de passer sur leurs compteurs de repli
(décision A1 du 9 novembre 2026). Aucune commande bloquante n'est employée.
"""

from functools import cache

import redis
from django.conf import settings


@cache
def client_redis() -> redis.Redis:
    delai = settings.REDIS_DELAI_S
    return redis.Redis.from_url(
        settings.REDIS_URL, socket_connect_timeout=delai, socket_timeout=delai
    )
