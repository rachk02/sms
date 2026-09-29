"""Worker accusés : applique au journal les accusés arrivés dans leur file.

Chaque accusé passe par `journal.services.appliquer_accuse`, qui corrèle,
vérifie la transition et écrit l'événement. Un accusé rejoué est sans effet,
si bien que la relivraison par le courtier ne produit jamais de doublon. Les
SMS entrants de `messages.entrants` arrivent avec T-28.
"""

from .base import Worker, journal, lancer


class WorkerAccuses(Worker):
    nom = "accuses"
    files = ("accuses.entrants", "messages.entrants")
    prefetch = 20

    async def traiter(self, message, canal, file_origine: str) -> None:
        from asgiref.sync import sync_to_async

        from apps.core.files import echouer
        from apps.journal.services import appliquer_accuse
        from apps.telecom.accuses import FILE, depuis_json

        if file_origine != self.topologie.nom(FILE):
            await echouer(canal, message, file_origine, "traitement prévu en T-28", self.topologie)
            return
        try:
            accuse = depuis_json(message.body)
            await sync_to_async(appliquer_accuse)(accuse)
        except Exception as erreur:
            journal.exception("Accusé non appliqué : %s", message.message_id)
            await echouer(canal, message, file_origine, repr(erreur)[:200], self.topologie)
            return
        await message.ack()


if __name__ == "__main__":
    lancer(WorkerAccuses)
