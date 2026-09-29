"""Accusés en transit dans la file `accuses.entrants`.

Le point d'entrée de rappel (T-28) y publie les accusés que les passerelles
poussent ; le worker accusés les en retire et les applique au journal.
"""

import json
from datetime import datetime

import aio_pika

from apps.core.files import TOPOLOGIE, Topologie

from .passerelles import Accuse

FILE = "accuses.entrants"


def vers_json(accuse: Accuse) -> bytes:
    return json.dumps(
        {
            "connecteur_id": str(accuse.connecteur_id),
            "reference_smsc": accuse.reference_smsc,
            "libelle_operateur": accuse.libelle_operateur,
            "etat": str(accuse.etat),
            "horodatage": accuse.horodatage.isoformat(),
        }
    ).encode()


def vers_dict(accuse: Accuse) -> dict:
    return json.loads(vers_json(accuse))


def depuis_json(corps: bytes) -> Accuse:
    """Accepte un accusé nu, ou l'enveloppe de la boîte d'envoi qui le porte
    dans `donnees` quand il vient du point d'entrée de rappel."""
    donnees = json.loads(corps)
    donnees = donnees.get("donnees", donnees)
    return Accuse(
        connecteur_id=donnees["connecteur_id"],
        reference_smsc=donnees["reference_smsc"],
        libelle_operateur=donnees["libelle_operateur"],
        etat=donnees["etat"],
        horodatage=datetime.fromisoformat(donnees["horodatage"]),
    )


async def publier(canal, accuse: Accuse, topologie: Topologie = TOPOLOGIE) -> None:
    """Publie un accusé dans la file des accusés. L'identifiant du message
    (connecteur, référence, état) rend un doublon reconnaissable."""
    echange = await canal.get_exchange(topologie.echange)
    await echange.publish(
        aio_pika.Message(
            vers_json(accuse),
            message_id=f"{accuse.connecteur_id}:{accuse.reference_smsc}:{accuse.etat}",
            content_type="application/json",
            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
        ),
        routing_key=topologie.nom(FILE),
    )
