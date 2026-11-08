"""Porte d'écriture du module api : l'idempotence des requêtes (T-108)."""

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta

from django.db import connection
from django.utils import timezone

from apps.core.cloisonnement import client_pose

from .models import Idempotence

DUREE = timedelta(hours=24)
LONGUEUR_MAX = 255


def empreinte(methode: str, chemin: str, corps: bytes) -> str:
    condensat = hashlib.sha256()
    for morceau in (methode.encode(), b"\n", chemin.encode(), b"\n", corps):
        condensat.update(morceau)
    return condensat.hexdigest()


@dataclass(frozen=True)
class Reservation:
    """Ce que la requête doit faire de sa clé d'idempotence : la traiter
    (`nouvelle`), rejouer la réponse gardée, ou refuser la clé réutilisée."""

    nouvelle: bool
    reutilisee: bool = False
    statut_http: int | None = None
    reponse: dict | None = None


def reserver(cle: str, empreinte_requete: str, *, maintenant=None) -> Reservation:
    """Réserve la clé pour le client posé, dans la transaction de la requête.

    L'insertion ignore le conflit sur `(client_id, cle)` : une requête
    concurrente portant la même clé attend sur l'index unique que la première
    soit validée, puis lit la réponse qu'elle a gardée. Aucune requête ne
    traite deux fois la même clé, et aucun état « en cours » n'est exposé."""
    maintenant = maintenant or timezone.now()
    client_id = client_pose()
    with connection.cursor() as curseur:
        curseur.execute(
            "INSERT INTO api_idempotence (id, client_id, cle, empreinte, cree_le, expire_le) "
            "VALUES (%s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (client_id, cle) DO NOTHING RETURNING id",
            [uuid.uuid7(), client_id, cle, empreinte_requete, maintenant, maintenant + DUREE],
        )
        if curseur.fetchone():
            return Reservation(nouvelle=True)
    gardee = Idempotence.objects.select_for_update().get(client_id=client_id, cle=cle)
    if gardee.expire_le <= maintenant:
        # Clé expirée, pas encore purgée : elle repart pour une nouvelle requête.
        gardee.empreinte, gardee.statut_http, gardee.reponse = empreinte_requete, None, None
        gardee.cree_le, gardee.expire_le = maintenant, maintenant + DUREE
        gardee.save()
        return Reservation(nouvelle=True)
    if gardee.empreinte != empreinte_requete:
        return Reservation(nouvelle=False, reutilisee=True)
    return Reservation(nouvelle=False, statut_http=gardee.statut_http, reponse=gardee.reponse)


def garder(cle: str, statut_http: int, reponse: dict) -> None:
    """Garde la réponse produite pour la clé réservée par cette requête."""
    Idempotence.objects.filter(client_id=client_pose(), cle=cle).update(
        statut_http=statut_http, reponse=reponse
    )


def purger(maintenant=None) -> int:
    """Efface les clés expirées. Appelée par l'ordonnanceur, sous le rôle
    worker, une fois par jour."""
    supprimees, _ = Idempotence.objects.filter(expire_le__lte=maintenant or timezone.now()).delete()
    return supprimees
