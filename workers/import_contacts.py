"""Worker d'import : lecture en flux, normalisation, dédoublonnage (T-47) ;
et export de contacts (T-53).

Une tâche longue à la fois (prefetch 1). Un import ou un export interrompu est
relivré par le courtier et repris depuis le début : rien n'avait été intégré.
"""

import json

from .base import Worker, journal, lancer


class WorkerImport(Worker):
    nom = "import"
    files = ("import.contacts",)
    prefetch = 1

    async def traiter(self, message, canal, file_origine: str) -> None:
        from asgiref.sync import sync_to_async

        from apps.contacts import imports, recherche
        from apps.core.files import echouer

        donnees = json.loads(message.body)
        donnees = donnees.get("donnees", donnees)
        # La même file porte les imports et les exports de contacts : deux
        # tâches longues sur le répertoire, une à la fois.
        if "export_id" in donnees:
            tache, objet_id = recherche.produire, donnees["export_id"]
        else:
            tache, objet_id = imports.traiter, donnees["import_id"]
        try:
            await sync_to_async(tache)(objet_id)
        except Exception as erreur:
            journal.exception("Tâche de contacts %s en échec.", objet_id)
            await echouer(canal, message, file_origine, repr(erreur)[:200], self.topologie)
            return
        await message.ack()


if __name__ == "__main__":
    lancer(WorkerImport)
