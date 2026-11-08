"""Worker webhook : livraison signée des événements destinés aux clients (T-111).

`webhooks.evenements` reçoit les événements `client.*` du bus ; chacun est
réparti, une fois, en une livraison par point de réception abonné
(`apps.api.webhooks.repartir`). `webhooks.sortants` reçoit un ordre par
livraison ; chaque tentative passe par `apps.api.webhooks.livrer`. Un échec
renvoie l'ordre au palier de réessai suivant ; après le dernier, l'ordre part
au rebut et la livraison est marquée abandonnée, visible du client.

`courriels.sortants` n'a pas encore de producteur : les courriels partent par
`apps.core.courriels`. Ce qui y arriverait repart au palier, sans se perdre.
"""

import json

from .base import Worker, journal, lancer


def _repartir(message) -> None:
    from apps.api.webhooks import repartir
    from apps.core.evenements import traiter_une_fois

    evenement = json.loads(message.body)
    traiter_une_fois(
        "webhooks.evenements",
        message.message_id or evenement["cle_idempotence"],
        lambda: repartir(evenement),
    )


def _livraison(message) -> str:
    evenement = json.loads(message.body)
    return evenement.get("donnees", evenement)["livraison_id"]


class WorkerWebhook(Worker):
    nom = "webhook"
    files = ("webhooks.evenements", "webhooks.sortants", "courriels.sortants")
    prefetch = 10

    async def traiter(self, message, canal, file_origine: str) -> None:
        from asgiref.sync import sync_to_async

        from apps.api.webhooks import LivraisonEchouee, abandonner, livrer
        from apps.core.files import echouer

        if file_origine == self.topologie.nom("courriels.sortants"):
            await echouer(
                canal,
                message,
                file_origine,
                "aucun traitement des courriels en file",
                self.topologie,
            )
            return
        try:
            if file_origine == self.topologie.nom("webhooks.evenements"):
                await sync_to_async(_repartir)(message)
            else:
                await sync_to_async(livrer)(_livraison(message))
        except Exception as erreur:
            if not isinstance(erreur, LivraisonEchouee):
                journal.exception("Webhook non traité : %s", message.message_id)
            destination = await echouer(
                canal, message, file_origine, repr(erreur)[:200], self.topologie
            )
            if destination == self.topologie.rebut and file_origine == self.topologie.nom(
                "webhooks.sortants"
            ):
                try:
                    await sync_to_async(abandonner)(
                        _livraison(message), "toutes les tentatives ont échoué"
                    )
                except Exception:
                    journal.exception("Livraison au rebut non marquée : %s", message.message_id)
            return
        await message.ack()


if __name__ == "__main__":
    lancer(WorkerWebhook)
