"""Files, échange et paliers de réessai, T-11.

Contre le vrai RabbitMQ, avec une topologie préfixée propre au test et des
paliers raccourcis : 300, 600, 900, 1200 et 1500 millisecondes.
"""

import asyncio
import time
import uuid

import aio_pika

from apps.core import bus
from apps.core.files import (
    ENTETE_ERREUR,
    ENTETE_FILE,
    ENTETE_PALIER,
    ENTETE_TENTATIVE,
    FILES,
    Topologie,
    declarer,
    echouer,
)

PALIERS_MS = (300, 600, 900, 1_200, 1_500)
TOLERANCE_S = 1.5


def _topologie():
    return Topologie(prefixe=f"test-{uuid.uuid4().hex[:8]}.", paliers_ms=PALIERS_MS)


async def _nettoyer(canal, topologie):
    for base in topologie.files:
        await canal.queue_delete(topologie.nom(base))
    for palier in range(1, len(topologie.paliers_ms) + 1):
        await canal.queue_delete(topologie.file_palier(palier))
    await canal.exchange_delete(topologie.echange)
    await canal.exchange_delete(topologie.echange_reessai)


async def _attendre(file, delai_s):
    fin = time.monotonic() + delai_s
    while time.monotonic() < fin:
        message = await file.get(fail=False)
        if message is not None:
            return message
        await asyncio.sleep(0.02)
    raise AssertionError(f"aucun message dans {file.name} après {delai_s} s")


def test_il_y_a_douze_files_de_trafic():
    assert len(FILES) == 12
    assert len(set(FILES)) == 12


def test_les_files_et_les_paliers_existent_apres_declaration():
    topologie = _topologie()

    async def scenario():
        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel()
            await declarer(canal, topologie)
            await declarer(canal, topologie)  # idempotent
            try:
                verification = await connexion.channel()
                for base in FILES:
                    await verification.declare_queue(topologie.nom(base), passive=True)
                for palier in range(1, 6):
                    await verification.declare_queue(topologie.file_palier(palier), passive=True)
                await verification.declare_exchange(topologie.echange, passive=True)
            finally:
                await _nettoyer(canal, topologie)

    asyncio.run(scenario())


def test_un_message_est_route_vers_la_file_qui_porte_son_nom():
    topologie = _topologie()

    async def scenario():
        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel()
            await declarer(canal, topologie)
            try:
                echange = await canal.get_exchange(topologie.echange)
                cible = topologie.nom("envoi.masse.orange")
                await echange.publish(aio_pika.Message(b"x"), routing_key=cible)
                message = await _attendre(await canal.get_queue(cible), 5)
                await message.ack()
                autre = await canal.get_queue(topologie.nom("envoi.masse.moov"))
                assert await autre.get(fail=False) is None
            finally:
                await _nettoyer(canal, topologie)

    asyncio.run(scenario())


def test_un_message_toujours_en_echec_suit_les_paliers_puis_finit_au_rebut():
    topologie = _topologie()

    async def scenario():
        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel()
            await declarer(canal, topologie)
            try:
                nom_file = topologie.nom("webhooks.sortants")
                file = await canal.get_queue(nom_file)
                echange = await canal.get_exchange(topologie.echange)
                await echange.publish(
                    aio_pika.Message(b"notification", message_id="cle-1"), routing_key=nom_file
                )

                message = await _attendre(file, 5)
                ecarts, destinations = [], []
                for palier_ms in PALIERS_MS:
                    destinations.append(
                        await echouer(canal, message, nom_file, "HTTP 503", topologie)
                    )
                    debut = time.monotonic()
                    message = await _attendre(file, palier_ms / 1000 + TOLERANCE_S)
                    ecarts.append(time.monotonic() - debut)
                # Échec après le dernier palier : le rebut, et plus aucune boucle.
                destinations.append(await echouer(canal, message, nom_file, "HTTP 503", topologie))
                rebut = await _attendre(await canal.get_queue(topologie.rebut), 5)
                await rebut.ack()
                await asyncio.sleep(max(PALIERS_MS) / 1000 + 0.5)
                assert await file.get(fail=False) is None
                return ecarts, destinations, rebut
            finally:
                await _nettoyer(canal, topologie)

    ecarts, destinations, rebut = asyncio.run(scenario())

    assert destinations == [*(topologie.file_palier(n) for n in range(1, 6)), topologie.rebut]
    for ecart, palier_ms in zip(ecarts, PALIERS_MS, strict=True):
        assert palier_ms / 1000 - 0.05 <= ecart < palier_ms / 1000 + TOLERANCE_S
    assert ecarts == sorted(ecarts)
    assert rebut.headers[ENTETE_TENTATIVE] == 6
    assert rebut.headers[ENTETE_FILE].endswith("webhooks.sortants")
    assert rebut.headers[ENTETE_ERREUR] == "HTTP 503"
    assert ENTETE_PALIER not in rebut.headers
    assert rebut.message_id == "cle-1"
    assert rebut.body == b"notification"
