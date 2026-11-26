"""Corrélation des accusés : identifiant de l'opérateur vers message interne.

À la soumission, le couple connecteur et identifiant externe est écrit en base
dans la transaction de la soumission, puis dans Redis, pour la validité du SMS
plus une marge (72 heures par défaut), une fois
cette transaction validée : Redis ne pointe jamais vers une soumission annulée.
À l'accusé, Redis répond en général, sinon la base prend le relais, y compris
quand Redis ne répond plus. Un vidage complet ou une panne de Redis ne coûte
donc que de la vitesse.
"""

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from redis.exceptions import RedisError

from apps.core.redis import client_redis, tolerer

from .models import AccuseOrphelin, Correlation

# Un accusé peut arriver jusqu'au terme de la validité du SMS, et un peu après :
# la corrélation vit cette validité plus une marge.
MARGE = timedelta(hours=24)


def validite() -> timedelta:
    return timedelta(hours=settings.VALIDITE_SMS_HEURES) + MARGE


PREFIXE = "sms:correlation"
journal = logging.getLogger(__name__)


@dataclass(frozen=True)
class Correspondance:
    message_id: object
    message_date_reception: datetime


def _redis():
    return client_redis()


def cle_redis(connecteur_id, reference_smsc: str) -> str:
    return f"{PREFIXE}:{connecteur_id}:{reference_smsc}"


def enregistrer(connecteur_id, reference_smsc: str, message_id, message_date_reception) -> None:
    Correlation.objects.create(
        connecteur_id=connecteur_id,
        reference_smsc=reference_smsc,
        message_id=message_id,
        message_date_reception=message_date_reception,
        expire_le=timezone.now() + validite(),
    )
    valeur = json.dumps({"message_id": str(message_id), "date": message_date_reception.isoformat()})
    cle = cle_redis(connecteur_id, reference_smsc)
    duree = validite()
    # La base fait foi : si Redis ne répond pas après la validation, la
    # soumission reste valide et l'accusé sera retrouvé en base.
    transaction.on_commit(
        lambda: tolerer(lambda: _redis().set(cle, valeur, ex=duree), "correlation")
    )


def retrouver(connecteur_id, reference_smsc: str) -> Correspondance | None:
    try:
        brut = _redis().get(cle_redis(connecteur_id, reference_smsc))
    except RedisError as erreur:
        journal.error("Redis indisponible, corrélation lue en base : %s", erreur)
        brut = None
    if brut is not None:
        valeur = json.loads(brut)
        return Correspondance(valeur["message_id"], parse_datetime(valeur["date"]))
    ligne = (
        Correlation.objects.filter(connecteur_id=connecteur_id, reference_smsc=reference_smsc)
        .values("message_id", "message_date_reception")
        .first()
    )
    if ligne is None:
        return None
    return Correspondance(ligne["message_id"], ligne["message_date_reception"])


def ranger_orphelin(accuse) -> AccuseOrphelin:
    return AccuseOrphelin.objects.create(
        connecteur_id=accuse.connecteur_id,
        reference_smsc=accuse.reference_smsc,
        charge_utile=json.loads(json.dumps(asdict(accuse), default=str)),
    )
