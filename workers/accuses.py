"""Worker accusés : applique au journal les accusés arrivés dans leur file.

Chaque accusé passe par `journal.services.appliquer_accuse`, qui corrèle,
vérifie la transition et écrit l'événement. Un accusé rejoué est sans effet,
si bien que la relivraison par le courtier ne produit jamais de doublon. Les
SMS entrants de `messages.entrants` sont écrits dans telecom_message_entrant ;
leur rapprochement avec un client et le STOP viennent au lot 6.
"""

import json
from datetime import datetime

from .base import Worker, journal, lancer


def _enregistrer_entrant(corps: bytes) -> None:
    from apps.telecom.services import enregistrer_entrant

    donnees = json.loads(corps)
    donnees = donnees.get("donnees", donnees)
    enregistrer_entrant(
        connecteur_id=donnees["connecteur_id"],
        reference=donnees["reference"],
        numero_expediteur=donnees["numero_expediteur"],
        numero_destination=donnees["numero_destination"],
        texte=donnees["texte"],
        recu_le=datetime.fromisoformat(donnees["recu_le"]),
    )


class WorkerAccuses(Worker):
    nom = "accuses"
    files = ("accuses.entrants", "messages.entrants")
    prefetch = 20

    async def traiter(self, message, canal, file_origine: str) -> None:
        from asgiref.sync import sync_to_async

        from apps.core.files import echouer
        from apps.journal.services import appliquer_accuse
        from apps.telecom.accuses import FILE, depuis_json

        try:
            if file_origine == self.topologie.nom(FILE):
                await sync_to_async(appliquer_accuse)(depuis_json(message.body))
            else:
                await sync_to_async(_enregistrer_entrant)(message.body)
        except Exception as erreur:
            journal.exception("Accusé non appliqué : %s", message.message_id)
            await echouer(canal, message, file_origine, repr(erreur)[:200], self.topologie)
            return
        await message.ack()


if __name__ == "__main__":
    lancer(WorkerAccuses)
