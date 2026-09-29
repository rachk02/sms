"""Socle commun des workers : consommation, arrêt propre, lancement.

À la réception du signal d'arrêt, un worker cesse de prendre de nouveaux
messages, laisse finir ceux en cours, qui s'acquittent eux-mêmes, puis sort. Un
message reçu mais pas encore commencé n'est pas acquitté : le courtier le rend
à la file à la fermeture du canal. Au-delà du délai de grâce, le worker sort
quand même ; le message interrompu sera relivré, et l'idempotence des
traitements fait le reste.
"""

import asyncio
import logging
import os
import signal

journal = logging.getLogger("workers")

DELAI_GRACE_S = 60


def preparer_django() -> None:
    """Charge Django avec le rôle PostgreSQL des workers."""
    os.environ["SMS_CONNEXION_PAR_DEFAUT"] = "WORKER"
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    import django

    django.setup()


def installer_arret(boucle: asyncio.AbstractEventLoop, arret: asyncio.Event) -> None:
    """SIGTERM et SIGINT déclenchent l'arrêt propre. `signal.signal` plutôt que
    `add_signal_handler`, que la boucle de Windows ne connaît pas."""

    def demander_arret(numero, _cadre):
        journal.info("Signal %s reçu, arrêt propre.", signal.Signals(numero).name)
        boucle.call_soon_threadsafe(arret.set)

    for numero in (signal.SIGTERM, signal.SIGINT):
        signal.signal(numero, demander_arret)


class Worker:
    """Consomme ses files et délègue chaque message à `traiter`."""

    nom = "worker"
    files: tuple[str, ...] = ()
    prefetch = 1
    delai_grace_s = DELAI_GRACE_S

    def __init__(self, topologie=None):
        from apps.core.files import TOPOLOGIE

        self.topologie = topologie or TOPOLOGIE
        self.arret: asyncio.Event | None = None
        # Levé quand les files sont déclarées et la consommation ouverte.
        self.pret = asyncio.Event()
        self.en_cours: set[asyncio.Task] = set()

    async def traiter(self, message, canal, file_origine: str) -> None:
        raise NotImplementedError

    def _recepteur(self, canal, file_origine):
        async def recevoir(message):
            tache = asyncio.current_task()
            self.en_cours.add(tache)
            try:
                await self.traiter(message, canal, file_origine)
            finally:
                self.en_cours.discard(tache)

        return recevoir

    async def executer(self, installer_signaux: bool = True) -> None:
        from asgiref.sync import sync_to_async
        from django.db import connections

        from apps.core import bus, files

        self.arret = asyncio.Event()
        if installer_signaux:
            installer_arret(asyncio.get_running_loop(), self.arret)
        connexion = await bus.connecter()
        try:
            canal = await connexion.channel()
            await canal.set_qos(prefetch_count=self.prefetch)
            await files.declarer(canal, self.topologie)
            abonnements = []
            for base in self.files:
                file = await canal.get_queue(self.topologie.nom(base))
                etiquette = await file.consume(self._recepteur(canal, file.name))
                abonnements.append((file, etiquette))
            journal.info("%s à l'écoute de %s.", self.nom, ", ".join(self.files))
            self.pret.set()

            await self.arret.wait()

            for file, etiquette in abonnements:
                await file.cancel(etiquette)
            if self.en_cours:
                journal.info("%s termine %d message(s) en cours.", self.nom, len(self.en_cours))
                _, restants = await asyncio.wait(set(self.en_cours), timeout=self.delai_grace_s)
                if restants:
                    journal.warning(
                        "%s sort avec %d message(s) interrompu(s).", self.nom, len(restants)
                    )
        finally:
            await connexion.close()
            await sync_to_async(connections.close_all)()
        journal.info("%s arrêté.", self.nom)


class WorkerEnAttente(Worker):
    """Worker dont le traitement arrive dans un lot suivant. Il consomme sa file
    et renvoie chaque message au palier de réessai, pour qu'aucun ne se perde."""

    lot = "suivant"

    async def traiter(self, message, canal, file_origine: str) -> None:
        from apps.core.files import echouer

        await echouer(
            canal, message, file_origine, f"traitement prévu au lot {self.lot}", self.topologie
        )


def lancer(classe: type[Worker]) -> None:
    # La journalisation JSON du projet est posée par django.setup().
    preparer_django()
    asyncio.run(classe().executer())
