"""Squelette des workers, T-12. Contre le vrai RabbitMQ."""

import asyncio
import signal
import time
import uuid

import aio_pika
import pytest

from apps.core import bus
from apps.core.files import ABONNEMENTS, ENTETE_TENTATIVE, FILES, Topologie
from workers.accuses import WorkerAccuses
from workers.base import Worker, WorkerEnAttente
from workers.envoi import WorkerEnvoi
from workers.ordonnanceur import CLE_VERROU, prendre_verrou

DUREE_TRAITEMENT_S = 1.5


def _topologie():
    return Topologie(prefixe=f"test-{uuid.uuid4().hex[:8]}.", paliers_ms=(200,) * 5)


async def _nettoyer(topologie):
    connexion = await bus.connecter()
    async with connexion:
        canal = await connexion.channel()
        for base in topologie.files:
            await canal.queue_delete(topologie.nom(base))
        for palier in range(1, len(topologie.paliers_ms) + 1):
            await canal.queue_delete(topologie.file_palier(palier))
        await canal.exchange_delete(topologie.echange)
        await canal.exchange_delete(topologie.echange_reessai)


class WorkerLent(Worker):
    nom = "lent"
    files = ("webhooks.sortants",)

    def __init__(self, topologie):
        super().__init__(topologie)
        self.commence = asyncio.Event()
        self.termines = []

    async def traiter(self, message, canal, file_origine):
        self.commence.set()
        await asyncio.sleep(DUREE_TRAITEMENT_S)
        await message.ack()
        self.termines.append(message.message_id)


@pytest.fixture
def signaux_restaures():
    avant = {numero: signal.getsignal(numero) for numero in (signal.SIGINT, signal.SIGTERM)}
    yield
    for numero, gestionnaire in avant.items():
        signal.signal(numero, gestionnaire)


def test_au_signal_d_arret_le_message_en_cours_finit_et_le_suivant_reste_en_file(
    signaux_restaures,
):
    topologie = _topologie()
    worker = WorkerLent(topologie)

    async def scenario():
        execution = asyncio.create_task(worker.executer())
        await asyncio.wait_for(worker.pret.wait(), 10)
        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel()
            echange = await canal.get_exchange(topologie.echange)
            file = topologie.nom("webhooks.sortants")
            for cle in ("premier", "second"):
                await echange.publish(aio_pika.Message(b"x", message_id=cle), routing_key=file)
            await asyncio.wait_for(worker.commence.wait(), 5)

            debut = time.monotonic()
            signal.raise_signal(signal.SIGINT)
            await asyncio.wait_for(execution, worker.delai_grace_s)
            duree_arret = time.monotonic() - debut

            restant = await (await canal.get_queue(file)).get(timeout=5)
            await restant.ack()
            return duree_arret, restant.message_id, restant.redelivered

    try:
        duree_arret, restant, _ = asyncio.run(scenario())
    finally:
        asyncio.run(_nettoyer(topologie))

    assert worker.termines == ["premier"]
    assert duree_arret < DUREE_TRAITEMENT_S + 2
    # Le second, jamais commencé, est resté dans la file pour le prochain worker.
    assert restant == "second"


def test_un_worker_en_attente_de_son_lot_ne_perd_aucun_message(signaux_restaures):
    topologie = _topologie()

    class Attente(WorkerEnAttente):
        files = ("import.contacts",)

    worker = Attente(topologie)

    async def scenario():
        execution = asyncio.create_task(worker.executer(installer_signaux=False))
        await asyncio.wait_for(worker.pret.wait(), 10)
        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel()
            echange = await canal.get_exchange(topologie.echange)
            await echange.publish(
                aio_pika.Message(b"x", message_id="k"),
                routing_key=topologie.nom("import.contacts"),
            )
            await asyncio.sleep(0.5)
            worker.arret.set()
            await asyncio.wait_for(execution, 10)
            # Rendu au palier, il revient dans sa file une fois la durée écoulée.
            file = await canal.get_queue(topologie.nom("import.contacts"))
            fin = time.monotonic() + 5
            relivre = None
            while relivre is None and time.monotonic() < fin:
                relivre = await file.get(fail=False)
                await asyncio.sleep(0.05)
            assert relivre is not None, "le message a été perdu"
            await relivre.ack()
            return relivre.headers[ENTETE_TENTATIVE]

    try:
        assert asyncio.run(scenario()) >= 1
    finally:
        asyncio.run(_nettoyer(topologie))


def test_chaque_point_d_entree_ecoute_ses_files():
    assert set(WorkerEnvoi.files) == {f for f in FILES if f.startswith("envoi.")}
    assert len(WorkerEnvoi.files) == 6
    assert WorkerAccuses.files == ("accuses.entrants", "messages.entrants", "contacts.operateurs")
    connues = set(FILES) | set(ABONNEMENTS)
    assert not {f for w in (WorkerEnvoi, WorkerAccuses) for f in w.files} - connues


@pytest.mark.django_db(databases=["default", "worker"])
def test_un_seul_ordonnanceur_prend_le_verrou():
    from django.db import connections

    try:
        assert prendre_verrou("default") is True
        assert prendre_verrou("worker") is False
    finally:
        with connections["default"].cursor() as curseur:
            curseur.execute("SELECT pg_advisory_unlock(%s)", [CLE_VERROU])
    assert prendre_verrou("worker") is True
    with connections["worker"].cursor() as curseur:
        curseur.execute("SELECT pg_advisory_unlock(%s)", [CLE_VERROU])
