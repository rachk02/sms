"""Worker d'import : lecture en flux, normalisation, dédoublonnage (T-47).

Une tâche longue à la fois (prefetch 1). Un import interrompu est relivré par
le courtier et repris depuis le début : rien n'avait été intégré.
"""

import json

from .base import Worker, journal, lancer


class WorkerImport(Worker):
    nom = "import"
    files = ("import.contacts",)
    prefetch = 1

    async def traiter(self, message, canal, file_origine: str) -> None:
        from asgiref.sync import sync_to_async

        from apps.contacts.imports import traiter
        from apps.core.files import echouer

        corps = json.loads(message.body)
        import_id = corps.get("donnees", corps)["import_id"]
        try:
            await sync_to_async(traiter)(import_id)
        except Exception as erreur:
            journal.exception("Import %s en échec.", import_id)
            await echouer(canal, message, file_origine, repr(erreur)[:200], self.topologie)
            return
        await message.ack()


if __name__ == "__main__":
    lancer(WorkerImport)
