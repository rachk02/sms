"""Débit maximal par connecteur, partagé par tous les processus d'envoi (T-63).

Chaque soumission retient un créneau auprès de Redis, espacé de 1/débit du
précédent pour ce connecteur (algorithme GCRA) : à dix messages par seconde,
les créneaux tombent toutes les cent millisecondes, jamais plus de dix dans une
même seconde, quel que soit le nombre de workers. L'horloge est celle de
Redis, la même pour tous les processus. Le worker retient son créneau et
l'attend juste avant l'appel à la passerelle, dans le fil qui soumet.
"""

from apps.core.redis import client_redis

CLE = "sms:debit:{connecteur_id}"
# Le débit accordé par l'opérateur est un plafond : les créneaux sont espacés
# de 3 % de plus que 1/débit, pour que l'imprécision des horloges et des temps
# de réponse ne fasse jamais tenir un message de trop dans une seconde.
MARGE = 1.03

# Renvoie l'attente en microsecondes avant le créneau retenu.
SCRIPT = """
local t = redis.call('TIME')
local maintenant = tonumber(t[1]) * 1000000 + tonumber(t[2])
local intervalle = tonumber(ARGV[1])
local prochain = tonumber(redis.call('GET', KEYS[1]) or maintenant)
if prochain < maintenant then prochain = maintenant end
local suivant = prochain + intervalle
redis.call('SET', KEYS[1], suivant, 'PX', math.floor((suivant - maintenant) / 1000) + 1000)
return prochain - maintenant
"""

_script = None


def creneau(connecteur) -> float:
    """Retient le prochain créneau du connecteur ; renvoie, en secondes,
    l'attente avant de pouvoir soumettre."""
    global _script
    if _script is None:
        _script = client_redis().register_script(SCRIPT)
    debit = max(1, connecteur.debit_max)
    attente_us = _script(
        keys=[CLE.format(connecteur_id=connecteur.id)], args=[int(1_000_000 * MARGE / debit)]
    )
    return int(attente_us) / 1_000_000
