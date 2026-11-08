"""Rapprochement quotidien du registre de crédits (T-80).

Pour chaque client, le solde acquis tenu par le cache (`credits_compte`) est
comparé à la somme du registre, et le montant réservé à la somme des
réservations actives. Tout écart, même d'un franc, lève une alerte qui nomme le
client et le montant. Rien n'est corrigé ici : une correction passe par une
écriture d'ajustement, motivée, décidée par un agent.

Une seule requête lit les trois tables : sous READ COMMITTED, elle les voit
toutes dans le même instantané, et une opération en cours (écriture au registre
et mise à jour du cache dans la même transaction) n'y fait pas apparaître de
faux écart. Appelé par l'ordonnanceur, sous le rôle worker, qui voit tous les
clients.
"""

import json
import logging
import time
from dataclasses import dataclass

from django.db import connection

from apps.core.evenements import publier

journal = logging.getLogger(__name__)

EVENEMENT = "credits.rapprochement.ecart"
# Le dernier passage, lu par la supervision du back-office (T-104) : le web ne
# relance pas la requête, il lit ce que le worker a constaté.
CLE_DERNIER = "sms:rapprochement:dernier"

ECARTS = """
WITH registre AS (
    SELECT client_id, SUM(montant) AS acquis FROM credits_registre GROUP BY client_id
), reserve AS (
    SELECT client_id, SUM(montant_restant) AS reserve
    FROM credits_reservation WHERE etat = 'ACTIVE' GROUP BY client_id
), clients AS (
    SELECT client_id FROM credits_compte
    UNION SELECT client_id FROM registre
    UNION SELECT client_id FROM reserve
)
SELECT k.client_id, cl.raison_sociale,
       COALESCE(c.solde_acquis, 0), COALESCE(r.acquis, 0),
       COALESCE(c.montant_reserve, 0), COALESCE(v.reserve, 0)
FROM clients k
JOIN comptes_client cl ON cl.id = k.client_id
LEFT JOIN credits_compte c ON c.client_id = k.client_id
LEFT JOIN registre r ON r.client_id = k.client_id
LEFT JOIN reserve v ON v.client_id = k.client_id
WHERE COALESCE(c.solde_acquis, 0) <> COALESCE(r.acquis, 0)
   OR COALESCE(c.montant_reserve, 0) <> COALESCE(v.reserve, 0)
ORDER BY cl.raison_sociale, k.client_id
"""


@dataclass(frozen=True)
class Ecart:
    client_id: object
    raison_sociale: str
    acquis_cache: int
    acquis_registre: int
    reserve_cache: int
    reserve_recalcule: int

    @property
    def ecart_acquis(self) -> int:
        """Ce que le cache annonce en trop (positif) ou en moins (négatif)."""
        return self.acquis_cache - self.acquis_registre

    @property
    def ecart_reserve(self) -> int:
        return self.reserve_cache - self.reserve_recalcule

    def __str__(self) -> str:
        return (
            f"Écart de rapprochement pour {self.raison_sociale} ({self.client_id}) : "
            f"solde acquis {self.acquis_cache} FCFA au cache, {self.acquis_registre} FCFA "
            f"au registre, écart {self.ecart_acquis:+d} FCFA ; réservé {self.reserve_cache} "
            f"FCFA au cache, {self.reserve_recalcule} FCFA recalculé, écart "
            f"{self.ecart_reserve:+d} FCFA."
        )


def ecarts() -> list[Ecart]:
    """Les clients dont le cache s'écarte du registre ou des réservations."""
    with connection.cursor() as curseur:
        curseur.execute(ECARTS)
        return [Ecart(*ligne) for ligne in curseur.fetchall()]


def rapprocher() -> list[Ecart]:
    """Passe sur tous les clients et alerte sur chaque écart : une ligne
    d'erreur au journal applicatif et un événement pour les notifications."""
    trouves = ecarts()
    for ecart in trouves:
        journal.error(str(ecart), extra={"client_id": str(ecart.client_id)})
        publier(
            EVENEMENT,
            client_id=ecart.client_id,
            objet_id=ecart.client_id,
            donnees={
                "client": ecart.raison_sociale,
                "acquis_cache": ecart.acquis_cache,
                "acquis_registre": ecart.acquis_registre,
                "ecart_acquis": ecart.ecart_acquis,
                "reserve_cache": ecart.reserve_cache,
                "reserve_recalcule": ecart.reserve_recalcule,
                "ecart_reserve": ecart.ecart_reserve,
            },
        )
    journal.info("Rapprochement du registre : %d écart(s).", len(trouves))
    from apps.core.redis import client_redis

    client_redis().set(
        CLE_DERNIER,
        json.dumps(
            {
                "le": int(time.time()),
                "ecarts": len(trouves),
                "clients": [e.raison_sociale for e in trouves[:10]],
            }
        ),
    )
    return trouves


def dernier() -> dict | None:
    """Le dernier rapprochement constaté : instant, nombre d'écarts, clients."""
    from apps.core.redis import client_redis

    brut = client_redis().get(CLE_DERNIER)
    return json.loads(brut) if brut else None
