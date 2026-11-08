"""Quotas de l'API publique, à deux niveaux (T-107).

**Par clé**, un seau à jetons dans Redis, en deux seaux tenus ensemble : une
limite en rafale (jetons par seconde) et une limite soutenue (jetons par
minute). Ils protègent la plateforme d'un intégrateur mal réglé. Chaque appel
coûte un jeton, quelle que soit sa taille ; un lot de mille messages est un
appel.

**Par compte**, le plafond horaire et journalier commun à toutes les clés
(`conformite.plafonds`, T-101), compté en messages. C'est lui qui borne ce
qu'une clé compromise peut envoyer : dix clés ne multiplient pas la limite. Un
dépassement est refusé et lève une alerte à l'exploitation.

Les réponses portent le restant des deux niveaux en en-tête, pour que
l'intégrateur n'apprenne pas la limite par un refus.

**Redis indisponible ne fait pas tomber l'API** : le quota passe en mode
permissif, l'appel est servi sans en-têtes de quota, et l'exploitation est
alertée (journal en erreur et événement `api.quota.permissif`, une fois par
minute et par processus au plus). Décision du lot 9, à confirmer : un quota
qui tombe laisse passer plutôt que de couper tous les clients pour une panne
de cache ; le plafond du compte, lui aussi dans Redis, tombe avec, et la
réservation de crédits continue de borner ce qui part.
"""

import logging
import math
import time
from dataclasses import dataclass, field

from django.conf import settings
from redis.exceptions import RedisError

from apps.core.redis import client_redis

journal = logging.getLogger(__name__)

CLE_SEAU = "sms:api:seau:{cle}:{seau}"
EVENEMENT_PERMISSIF = "api.quota.permissif"
_ALERTE_TOUTES_LES_S = 60
_derniere_alerte = float("-inf")

# Deux seaux tenus ensemble : un jeton n'est pris que si les deux en ont.
# Rend accepté (0 ou 1), les jetons restants de chaque seau, et l'attente en
# millisecondes avant qu'un jeton soit disponible dans les deux.
_SCRIPT = """
local maintenant = tonumber(ARGV[1])
local cout = tonumber(ARGV[2])
local capacites = {tonumber(ARGV[3]), tonumber(ARGV[5])}
local taux = {tonumber(ARGV[4]), tonumber(ARGV[6])}
local jetons = {}
for i = 1, 2 do
  local lu = redis.call('HMGET', KEYS[i], 'jetons', 'instant')
  local restant = tonumber(lu[1])
  local depuis = tonumber(lu[2])
  if restant == nil then
    restant = capacites[i]
    depuis = maintenant
  end
  jetons[i] = math.min(capacites[i], restant + math.max(0, maintenant - depuis) * taux[i])
end
local accepte = jetons[1] >= cout and jetons[2] >= cout
local attente = 0
for i = 1, 2 do
  if accepte then
    jetons[i] = jetons[i] - cout
  elseif jetons[i] < cout then
    attente = math.max(attente, math.ceil((cout - jetons[i]) / taux[i]))
  end
  redis.call('HSET', KEYS[i], 'jetons', tostring(jetons[i]), 'instant', tostring(maintenant))
  redis.call('PEXPIRE', KEYS[i], math.ceil(capacites[i] / taux[i]) + 1000)
end
return {accepte and 1 or 0, math.floor(jetons[1]), math.floor(jetons[2]), attente}
"""


def reglage() -> dict:
    return settings.API_QUOTAS


@dataclass
class Decision:
    accepte: bool = True
    # Restants par niveau ; vide en mode permissif.
    entetes: dict = field(default_factory=dict)
    attente_s: int = 0
    permissif: bool = False


def _alerter_permissif(erreur: Exception) -> None:
    """Journal en erreur et événement à l'exploitation, une fois par minute au
    plus : une panne de Redis ne doit pas noyer la boîte d'envoi."""
    global _derniere_alerte
    journal.error("Quota de l'API en mode permissif : Redis indisponible (%s).", erreur)
    if time.monotonic() - _derniere_alerte < _ALERTE_TOUTES_LES_S:
        return
    _derniere_alerte = time.monotonic()
    from django.db import transaction

    from apps.core.evenements import publier

    try:
        # Dans un point de sauvegarde : un échec d'écriture n'abîme pas la
        # transaction de la requête, qui doit continuer d'être servie.
        with transaction.atomic():
            publier(EVENEMENT_PERMISSIF, donnees={"erreur": str(erreur)[:200]})
    except Exception:  # noqa: BLE001 - l'alerte ne doit jamais faire tomber l'appel
        journal.exception("Alerte du mode permissif non publiée.")


def prendre(cle_id, *, maintenant_ms: int | None = None, cout: int = 1) -> Decision:
    """Prend un jeton dans les deux seaux de la clé."""
    regle = reglage()
    rafale, minute = regle["rafale_par_seconde"], regle["soutenu_par_minute"]
    maintenant_ms = maintenant_ms if maintenant_ms is not None else int(time.time() * 1000)
    try:
        script = client_redis().register_script(_SCRIPT)
        accepte, reste_rafale, reste_minute, attente_ms = script(
            keys=[
                CLE_SEAU.format(cle=cle_id, seau="seconde"),
                CLE_SEAU.format(cle=cle_id, seau="minute"),
            ],
            args=[maintenant_ms, cout, rafale, rafale / 1000, minute, minute / 60_000],
        )
    except RedisError as erreur:
        _alerter_permissif(erreur)
        return Decision(permissif=True)
    return Decision(
        accepte=bool(accepte),
        attente_s=max(1, math.ceil(attente_ms / 1000)) if not accepte else 0,
        entetes={
            "Quota-Cle-Seconde-Limite": str(rafale),
            "Quota-Cle-Seconde-Restant": str(reste_rafale),
            "Quota-Cle-Minute-Limite": str(minute),
            "Quota-Cle-Minute-Restant": str(reste_minute),
        },
    )


def entetes_compte(client) -> dict:
    """Le restant du plafond du compte, en messages, pour l'heure et le jour en
    cours. Vide si Redis ne répond pas."""
    from apps.conformite.plafonds import essais

    try:
        tentes = essais(client.id)
    except RedisError as erreur:
        _alerter_permissif(erreur)
        return {}
    return {
        "Quota-Compte-Heure-Limite": str(client.plafond_horaire),
        "Quota-Compte-Heure-Restant": str(max(0, client.plafond_horaire - tentes["horaire"])),
        "Quota-Compte-Jour-Limite": str(client.plafond_journalier),
        "Quota-Compte-Jour-Restant": str(max(0, client.plafond_journalier - tentes["journalier"])),
    }
