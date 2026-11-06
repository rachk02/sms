"""Worker paiements : vérifie chaque rappel de paiement arrivé dans sa file.

Le point d'entrée public (`apps/paiements/rappels.py`) n'a fait que filtrer et
déposer. Ici, chaque rappel passe par `paiements.services.verifier`, qui
interroge l'API de statut du fournisseur, puis crédite sous verrou, une seule
fois. Un rappel rejoué, réémis ou relivré par le courtier est sans effet. Une
erreur, réseau du fournisseur compris, renvoie le rappel au palier de réessai.
"""

import json

from .base import Worker, journal, lancer


def _verifier(corps: bytes) -> None:
    from apps.paiements.services import verifier

    evenement = json.loads(corps)
    donnees = evenement.get("donnees", evenement)
    verifier(donnees["fournisseur"], donnees["reference"])


class WorkerPaiements(Worker):
    nom = "paiements"
    files = ("paiements.rappels",)
    prefetch = 10

    async def traiter(self, message, canal, file_origine: str) -> None:
        from asgiref.sync import sync_to_async

        from apps.core.files import echouer

        try:
            await sync_to_async(_verifier)(message.body)
        except Exception as erreur:
            journal.exception("Rappel de paiement non vérifié : %s", message.message_id)
            await echouer(canal, message, file_origine, repr(erreur)[:200], self.topologie)
            return
        await message.ack()


if __name__ == "__main__":
    lancer(WorkerPaiements)
