"""Liaison du bus d'événements avec RabbitMQ.

Deux rôles, tous deux tenus par des workers :

- le relais lit la boîte d'envoi et publie chaque événement sur l'échange
  `evenements`, de type sujet, avec le nom de l'événement pour clé de routage ;
- le consommateur reçoit un événement, le traite par `traiter_une_fois`, et
  ne l'acquitte qu'après la validation de la transaction, jamais avant.

Le relais peut publier deux fois le même événement (mort entre la publication
et le marquage). C'est sans conséquence : la clé d'idempotence voyage comme
identifiant du message et le consommateur reconnaît le doublon.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import aio_pika
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import DEFAULT_DB_ALIAS
from django.utils import timezone

from .evenements import traiter_une_fois
from .files import TOPOLOGIE, Topologie, echouer
from .models import EvenementSortant

ECHANGE = "evenements"

journal = logging.getLogger(__name__)


async def connecter() -> aio_pika.abc.AbstractRobustConnection:
    return await aio_pika.connect_robust(settings.RABBITMQ_URL)


async def declarer_echange(canal) -> aio_pika.abc.AbstractExchange:
    return await canal.declare_exchange(ECHANGE, aio_pika.ExchangeType.TOPIC, durable=True)


def _a_publier(using: str, lot: int) -> list[dict]:
    return list(
        EvenementSortant.objects.using(using)
        .filter(publie_le__isnull=True)
        .order_by("cree_le", "id")
        .values("id", "nom", "client_id", "objet_id", "auteur_id", "donnees", "cle_idempotence")[
            :lot
        ]
    )


def _marquer_publies(using: str, identifiants: list) -> None:
    EvenementSortant.objects.using(using).filter(id__in=identifiants).update(
        publie_le=timezone.now()
    )


async def relayer(canal, *, using: str = DEFAULT_DB_ALIAS, lot: int = 100) -> int:
    """Publie un lot d'événements en attente et renvoie leur nombre."""
    echange = await declarer_echange(canal)
    evenements = await sync_to_async(_a_publier)(using, lot)
    for evenement in evenements:
        corps = json.dumps(
            {cle: valeur for cle, valeur in evenement.items() if cle != "id"}, default=str
        ).encode()
        await echange.publish(
            aio_pika.Message(
                corps,
                message_id=evenement["cle_idempotence"],
                content_type="application/json",
                delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
            ),
            routing_key=evenement["nom"],
        )
    if evenements:
        await sync_to_async(_marquer_publies)(using, [e["id"] for e in evenements])
    return len(evenements)


async def traiter_message(
    message: aio_pika.abc.AbstractIncomingMessage,
    consommateur: str,
    gestionnaire: Callable[[dict], Any],
    *,
    canal,
    file_origine: str,
    topologie: Topologie = TOPOLOGIE,
    using: str = DEFAULT_DB_ALIAS,
) -> bool:
    """Traite un événement reçu au plus une fois, puis l'acquitte.

    En cas d'échec, le message part au palier de réessai suivant, ou au rebut
    après le dernier (voir `apps.core.files`). Renvoie vrai si le traitement a
    eu lieu, faux s'il avait déjà eu lieu ou s'il a échoué.
    """
    evenement = json.loads(message.body)
    cle = message.message_id or evenement["cle_idempotence"]
    try:
        traite = await sync_to_async(traiter_une_fois)(
            consommateur, cle, lambda: gestionnaire(evenement), using
        )
    except Exception as erreur:
        journal.exception("Échec du traitement de %s par %s", cle, consommateur)
        await echouer(canal, message, file_origine, repr(erreur)[:200], topologie)
        return False
    await message.ack()
    return traite
