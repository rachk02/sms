"""Compteurs de repli en mémoire quand Redis ne répond pas (décision A1 du
9 novembre 2026).

Les quotas de l'API (seaux par clé) et le plafond du compte comptent dans
Redis. Si Redis tombe, ils ne laissent pas tout passer : ils comptent ici, dans
la mémoire du processus, aux mêmes limites. Le compte est approximatif (chaque
processus a le sien, et il repart de zéro au redémarrage), jamais illimité.

La connexion et le second facteur n'ont pas de repli : sans Redis, ils restent
fermés.

Après un échec, Redis est laissé de côté pendant cinq secondes : le compte se
fait directement en mémoire, sans attendre à chaque appel le délai de
connexion d'un Redis qui ne répond plus. Passé ce temps, Redis est retenté.

Chaque échec est signalé : journal en erreur, et événement `api.quota.repli` à
l'exploitation, au plus une fois par minute et par processus, pour qu'une
panne ne noie pas la boîte d'envoi.
"""

import logging
import math
import threading
import time

from redis.exceptions import RedisError

journal = logging.getLogger(__name__)

EVENEMENT = "api.quota.repli"
_ALERTE_TOUTES_LES_S = 60
PAUSE_APRES_ECHEC_S = 5
_dernier_echec = float("-inf")

_verrou = threading.Lock()
# clé : (valeur, instant d'expiration en secondes monotones)
_compteurs: dict[str, tuple[int, float]] = {}
# clé : (jetons, instant en millisecondes, instant d'expiration monotone)
_seaux: dict[str, tuple[float, int, float]] = {}
_derniere_alerte = float("-inf")


def vider() -> None:
    """Remet tout à zéro, pour les tests."""
    global _derniere_alerte, _dernier_echec
    with _verrou:
        _compteurs.clear()
        _seaux.clear()
        _derniere_alerte = _dernier_echec = float("-inf")


def tenter(par_redis, en_memoire):
    """Rend (résultat, repli) : `par_redis()` si Redis répond, sinon
    `en_memoire()`. Dans les cinq secondes qui suivent un échec, Redis n'est
    pas retenté."""
    global _dernier_echec
    if time.monotonic() - _dernier_echec >= PAUSE_APRES_ECHEC_S:
        try:
            return par_redis(), False
        except RedisError as erreur:
            with _verrou:
                _dernier_echec = time.monotonic()
            alerter(erreur)
    return en_memoire(), True


def _purger(table: dict, maintenant: float) -> None:
    for cle in [cle for cle, valeur in table.items() if valeur[-1] <= maintenant]:
        del table[cle]


def incrementer(cle: str, nombre: int, duree_s: int) -> int:
    """Comme INCRBY suivi d'EXPIRE : rend la nouvelle valeur. Les clés échues
    disparaissent, si bien qu'une longue panne ne garde pas chaque heure."""
    maintenant = time.monotonic()
    with _verrou:
        _purger(_compteurs, maintenant)
        valeur = _compteurs.get(cle, (0, 0.0))[0] + nombre
        _compteurs[cle] = (valeur, maintenant + duree_s)
        return valeur


def lire(cle: str) -> int:
    with _verrou:
        valeur, expire = _compteurs.get(cle, (0, 0.0))
        return valeur if expire > time.monotonic() else 0


def prendre_jeton(
    cles: list[str], capacites: list[float], taux: list[float], maintenant_ms: int, cout: int
) -> tuple[bool, list[int], int]:
    """Le même calcul que le script Lua des quotas, sur deux seaux tenus
    ensemble (taux en jetons par milliseconde). Rend accepté, les jetons
    restants de chaque seau et l'attente en millisecondes."""
    horloge = time.monotonic()
    with _verrou:
        _purger(_seaux, horloge)
        jetons = []
        for cle, capacite, debit in zip(cles, capacites, taux, strict=True):
            restant, depuis, _ = _seaux.get(cle, (capacite, maintenant_ms, 0.0))
            jetons.append(min(capacite, restant + max(0, maintenant_ms - depuis) * debit))
        accepte = all(j >= cout for j in jetons)
        attente = 0
        for i, (cle, capacite, debit) in enumerate(zip(cles, capacites, taux, strict=True)):
            if accepte:
                jetons[i] -= cout
            elif jetons[i] < cout:
                attente = max(attente, math.ceil((cout - jetons[i]) / debit))
            vie_s = (math.ceil(capacite / debit) + 1000) / 1000
            _seaux[cle] = (jetons[i], maintenant_ms, horloge + vie_s)
        return accepte, [math.floor(j) for j in jetons], attente


def alerter(erreur: Exception) -> None:
    """Journal en erreur, et événement à l'exploitation une fois par minute au
    plus."""
    global _derniere_alerte
    journal.error("Quotas en repli sur la mémoire du processus : Redis indisponible (%s).", erreur)
    with _verrou:
        if time.monotonic() - _derniere_alerte < _ALERTE_TOUTES_LES_S:
            return
        _derniere_alerte = time.monotonic()
    from django.db import transaction

    from apps.core.evenements import publier

    try:
        # Dans un point de sauvegarde : un échec d'écriture n'abîme pas la
        # transaction de la requête, qui doit continuer d'être servie.
        with transaction.atomic():
            publier(EVENEMENT, donnees={"erreur": str(erreur)[:200]})
    except Exception:  # noqa: BLE001 - l'alerte ne doit jamais faire tomber l'appel
        journal.exception("Alerte du repli des quotas non publiée.")
