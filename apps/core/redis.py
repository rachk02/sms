"""Client Redis partagé. Redis est un cache : il ne détient rien d'unique."""

from functools import cache

import redis
from django.conf import settings


@cache
def client_redis() -> redis.Redis:
    return redis.Redis.from_url(settings.REDIS_URL)
