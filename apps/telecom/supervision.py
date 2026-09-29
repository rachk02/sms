"""État des liaisons, publié dans Redis pour qui n'a pas accès aux connecteurs.

La table des connecteurs porte les secrets des liaisons SMPP : seuls les
workers la lisent. L'ordonnanceur en recopie à chaque tour les seuls champs
sans secret dans un hachage Redis, où le service web les lit pour ses
métriques. Le hachage expire si l'ordonnanceur cesse de le rafraîchir : une
supervision muette se voit, elle ne ment pas.
"""

import json

from django.utils import timezone

from apps.core.redis import client_redis

from .models import Connecteur

CLE_LIAISONS = "sms:supervision:liaisons"
CLE_PUBLIEES_LE = "sms:supervision:liaisons:publiees_le"
DUREE_DE_VIE_S = 120


def publier_etat_liaisons() -> int:
    """Lecture par le rôle worker, écriture dans Redis. Renvoie le nombre de liaisons."""
    liaisons = {
        str(ligne["id"]): json.dumps(
            {
                "nom": ligne["nom"],
                "operateur": ligne["operateur"],
                "etat": ligne["etat_liaison"],
                "actif": ligne["actif"],
            }
        )
        for ligne in Connecteur.objects.values("id", "nom", "operateur", "etat_liaison", "actif")
    }
    redis = client_redis()
    with redis.pipeline() as lot:
        lot.delete(CLE_LIAISONS)
        if liaisons:
            lot.hset(CLE_LIAISONS, mapping=liaisons)
            lot.expire(CLE_LIAISONS, DUREE_DE_VIE_S)
        lot.set(CLE_PUBLIEES_LE, int(timezone.now().timestamp()), ex=DUREE_DE_VIE_S)
        lot.execute()
    return len(liaisons)


def etat_publie() -> tuple[dict[str, dict], int | None]:
    """Les liaisons publiées, par identifiant de connecteur, et l'instant de
    publication en secondes depuis l'époque, ou None si rien n'est publié."""
    redis = client_redis()
    brut = redis.hgetall(CLE_LIAISONS)
    publiees_le = redis.get(CLE_PUBLIEES_LE)
    liaisons = {cle.decode(): json.loads(valeur) for cle, valeur in brut.items()}
    return liaisons, int(publiees_le) if publiees_le else None
