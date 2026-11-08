"""Bus d'événements internes : publication et traitement idempotent.

Une couche basse qui doit signaler quelque chose à une couche haute publie un
événement, elle n'importe rien. Les noms suivent la convention domaine, objet,
action : `compte.dossier.approuve`, `campagne.terminee`, `credit.seuil.franchi`.

La publication écrit dans la boîte d'envoi (`core_evenement_sortant`), dans la
transaction en cours ; le relais (`apps.core.bus`) la publie ensuite sur
RabbitMQ. Le courtier garantit au moins une livraison, jamais exactement une :
chaque consommateur passe donc par `traiter_une_fois`.
"""

import uuid
from collections.abc import Callable
from typing import Any

from django.db import DEFAULT_DB_ALIAS, IntegrityError, transaction

from .models import EvenementSortant, EvenementTraite


def publier(
    nom: str,
    *,
    client_id=None,
    objet_id=None,
    auteur_id=None,
    donnees: dict | None = None,
    cle_idempotence: str | None = None,
    echange: str = "evenements",
    using: str = DEFAULT_DB_ALIAS,
) -> str:
    """Enregistre un événement à publier et renvoie sa clé d'idempotence.

    Une seconde publication avec la même clé est sans effet : l'événement
    n'existe qu'une fois dans la boîte d'envoi.
    """
    cle = cle_idempotence or str(uuid.uuid7())
    EvenementSortant.objects.using(using).bulk_create(
        [
            EvenementSortant(
                nom=nom,
                client_id=client_id,
                objet_id=objet_id,
                auteur_id=auteur_id,
                donnees=donnees or {},
                cle_idempotence=cle,
                echange=echange,
            )
        ],
        ignore_conflicts=True,
    )
    return cle


def publier_lot(evenements: list[dict], *, using: str = DEFAULT_DB_ALIAS) -> list[str]:
    """Plusieurs `publier` en une insertion (un lot de l'API, T-108). Chaque
    dictionnaire porte les arguments nommés de `publier`, `nom` compris ; mêmes
    règles : une clé déjà publiée est sans effet."""
    lignes = [
        EvenementSortant(
            nom=evenement["nom"],
            client_id=evenement.get("client_id"),
            objet_id=evenement.get("objet_id"),
            auteur_id=evenement.get("auteur_id"),
            donnees=evenement.get("donnees") or {},
            cle_idempotence=evenement.get("cle_idempotence") or str(uuid.uuid7()),
            echange=evenement.get("echange", "evenements"),
        )
        for evenement in evenements
    ]
    EvenementSortant.objects.using(using).bulk_create(lignes, ignore_conflicts=True)
    return [ligne.cle_idempotence for ligne in lignes]


def traiter_une_fois(
    consommateur: str,
    cle_idempotence: str,
    traitement: Callable[[], Any],
    using: str = DEFAULT_DB_ALIAS,
) -> bool:
    """Exécute `traitement` si ce consommateur n'a pas encore traité cette clé.

    La trace et le traitement sont validés dans la même transaction : si le
    traitement échoue, la trace disparaît avec lui et l'événement pourra être
    rejoué ; s'il réussit, un rejeu ultérieur est reconnu et ignoré, même si
    le processus est mort avant d'acquitter le message. Renvoie vrai si le
    traitement a eu lieu.
    """
    try:
        with transaction.atomic(using=using):
            EvenementTraite.objects.using(using).create(
                consommateur=consommateur, cle_idempotence=cle_idempotence
            )
            traitement()
    except IntegrityError:
        if (
            EvenementTraite.objects.using(using)
            .filter(consommateur=consommateur, cle_idempotence=cle_idempotence)
            .exists()
        ):
            return False
        raise
    return True
