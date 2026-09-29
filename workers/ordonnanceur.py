"""Ordonnanceur : relais des événements, partitions à venir, puis au fil des lots
les envois programmés, les campagnes en attente de fenêtre et les rapprochements.

Un seul exemplaire actif. Le verrou consultatif PostgreSQL pris au démarrage
l'assure : un second exemplaire échoue à le prendre et reste en veille, prêt à
reprendre si le premier meurt, sa session et donc son verrou disparaissant avec
lui.
"""

import asyncio
import time

from .base import Worker, installer_arret, journal, lancer

CLE_VERROU = 72_016_001


def prendre_verrou(using: str = "default") -> bool:
    from django.db import connections

    with connections[using].cursor() as curseur:
        curseur.execute("SELECT pg_try_advisory_lock(%s)", [CLE_VERROU])
        return curseur.fetchone()[0]


class Ordonnanceur(Worker):
    nom = "ordonnanceur"
    attente_verrou_s = 5.0
    periode_relais_s = 1.0
    periode_partitions_s = 3_600.0

    async def _attendre(self, secondes: float) -> None:
        try:
            await asyncio.wait_for(self.arret.wait(), timeout=secondes)
        except TimeoutError:
            pass

    async def executer(self, installer_signaux: bool = True) -> None:
        from asgiref.sync import sync_to_async
        from django.db import connections

        from apps.core import bus
        from apps.journal.services import creer_partitions_a_venir

        self.arret = asyncio.Event()
        if installer_signaux:
            installer_arret(asyncio.get_running_loop(), self.arret)
        try:
            while not self.arret.is_set():
                if await sync_to_async(prendre_verrou)():
                    break
                journal.info("Ordonnanceur en veille : un autre exemplaire est actif.")
                await self._attendre(self.attente_verrou_s)
            if self.arret.is_set():
                return
            journal.info("Ordonnanceur actif.")
            connexion = await bus.connecter()
            try:
                canal = await connexion.channel()
                derniere_partition = 0.0
                while not self.arret.is_set():
                    if time.monotonic() - derniere_partition >= self.periode_partitions_s:
                        await sync_to_async(creer_partitions_a_venir)()
                        derniere_partition = time.monotonic()
                    await bus.relayer(canal)
                    await self._attendre(self.periode_relais_s)
            finally:
                await connexion.close()
        finally:
            await sync_to_async(connections.close_all)()
            journal.info("Ordonnanceur arrêté.")


if __name__ == "__main__":
    lancer(Ordonnanceur)
