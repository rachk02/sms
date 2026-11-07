"""Plafond horaire et journalier d'un compte (T-101).

Le plafond porte sur les envois hors campagne, ceux d'un envoi unitaire et,
au lot 9, de l'API : il borne ce qu'une clé compromise peut envoyer avant qu'on
s'en aperçoive. Il est commun à tout le compte, quelle que soit la clé : créer
dix clés ne multiplie pas la limite. Une campagne en est exclue : elle est
confirmée par son auteur, chiffres lus, et bornée par la réservation de son
coût.

Les essais se comptent dans Redis, par heure et par jour. Un essai au-delà du
plafond est refusé ; le premier refus ouvre une alerte à l'exploitation, et
quand les essais atteignent le seuil critique de la règle PLAFOND_COMPTE, en
pour cent du plafond, l'alerte devient critique et le compte est suspendu
automatiquement (`backoffice.services.suspendre_sur_alertes`, par
l'ordonnanceur).
"""

import math

from django.utils import timezone

from apps.core.redis import client_redis

from . import fraude
from .models import AlerteFraude, RegleFraude

CLE = "sms:plafond:{client}:{periode}:{instant}"
DUREES_S = {"horaire": 2 * 3600, "journalier": 2 * 86400}


class PlafondAtteint(Exception):
    def __init__(self, periode: str, plafond: int):
        self.periode, self.plafond = periode, plafond
        super().__init__(
            f"Plafond {periode} du compte atteint : {plafond} envois. Les envois reprendront "
            "à la période suivante ; pour relever le plafond, contactez EL-IMMEKA."
        )


def _cles(client_id, maintenant) -> dict:
    return {
        "horaire": CLE.format(client=client_id, periode="h", instant=f"{maintenant:%Y%m%d%H}"),
        "journalier": CLE.format(client=client_id, periode="j", instant=f"{maintenant:%Y%m%d}"),
    }


def consommer(client, *, maintenant=None, cle_api: str | None = None) -> None:
    """Compte un envoi tenté ; lève PlafondAtteint au-delà d'un plafond.
    `cle_api` est notée dans l'alerte, jamais dans le compte : le plafond est
    celui du compte."""
    maintenant = maintenant or timezone.now()
    cles = _cles(client.id, maintenant)
    redis = client_redis()
    with redis.pipeline() as lot:
        for periode, cle in cles.items():
            lot.incr(cle)
            lot.expire(cle, DUREES_S[periode])
        resultats = lot.execute()
    essais = {"horaire": resultats[0], "journalier": resultats[2]}
    plafonds = {"horaire": client.plafond_horaire, "journalier": client.plafond_journalier}
    for periode in ("horaire", "journalier"):
        if essais[periode] > plafonds[periode]:
            _signaler(client, periode, essais[periode], plafonds[periode], cle_api)
            raise PlafondAtteint(periode, plafonds[periode])


def essais(client_id, *, maintenant=None) -> dict:
    """Les envois tentés de l'heure et du jour en cours."""
    cles = _cles(client_id, maintenant or timezone.now())
    valeurs = client_redis().mget(list(cles.values()))
    return {periode: int(v or 0) for periode, v in zip(cles, valeurs, strict=True)}


def seuil_critique(plafond: int, regle: RegleFraude | None) -> int | None:
    if regle is None:
        return None
    return math.ceil(plafond * regle.seuil_critique / 100)


def _signaler(client, periode: str, essais: int, plafond: int, cle_api) -> None:
    """Une alerte au premier refus, une seconde, critique, au seuil critique :
    deux écritures par période au plus, quel que soit le nombre de refus."""
    regle = fraude.regle(RegleFraude.Code.PLAFOND_COMPTE)
    critique = seuil_critique(plafond, regle)
    if essais == plafond + 1:
        gravite = AlerteFraude.Gravite.ALERTE
    elif critique is not None and essais == max(critique, plafond + 2):
        gravite = AlerteFraude.Gravite.CRITIQUE
    else:
        return
    fraude.signaler(
        client.id,
        RegleFraude.Code.PLAFOND_COMPTE,
        gravite=gravite,
        valeur=essais,
        details={
            "periode": periode,
            "plafond": plafond,
            "essais": essais,
            "seuil_critique": critique,
            **({"cle_api": cle_api} if cle_api else {}),
        },
    )
