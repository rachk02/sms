"""Battements des workers (T-96).

Chaque exemplaire de worker écrit dans Redis, toutes les quinze secondes, une
clé qui expire au bout d'une minute : son nom, son hôte et son processus,
l'heure de son démarrage, son état et ses messages en cours. Un worker arrêté
proprement efface sa clé ; un worker tombé laisse la sienne expirer. Le
back-office lit ces clés et nomme les workers attendus dont aucun exemplaire
ne bat.
"""

import json
import os
import socket
import time

from apps.core.redis import client_redis

PREFIXE = "sms:workers:"
PERIODE_S = 15
DUREE_DE_VIE_S = 60

# Les workers déployés, par leur attribut `nom` (workers/*.py). Un test
# vérifie que la liste suit les classes.
ATTENDUS = ("ordonnanceur", "envoi", "accuses", "import", "paiements", "webhook")


def exemplaire() -> str:
    return f"{socket.gethostname()}:{os.getpid()}"


def battre(nom: str, ident: str, *, demarre_le: float, etat: str, en_cours: int) -> None:
    valeur = {
        "nom": nom,
        "exemplaire": ident,
        "demarre_le": int(demarre_le),
        "vu_le": int(time.time()),
        "etat": etat,
        "en_cours": en_cours,
    }
    client_redis().set(f"{PREFIXE}{nom}:{ident}", json.dumps(valeur), ex=DUREE_DE_VIE_S)


def oublier(nom: str, ident: str) -> None:
    client_redis().delete(f"{PREFIXE}{nom}:{ident}")


def lire() -> list[dict]:
    """Les exemplaires vivants, triés par nom puis par exemplaire."""
    redis = client_redis()
    cles = list(redis.scan_iter(match=f"{PREFIXE}*", count=200))
    valeurs = redis.mget(cles) if cles else []
    vivants = [json.loads(v) for v in valeurs if v]
    return sorted(vivants, key=lambda b: (b["nom"], b["exemplaire"]))
