"""Bus d'événements, T-10.

Les derniers tests passent par le vrai RabbitMQ du docker-compose.
"""

import asyncio
import json
import uuid

import aio_pika
import pytest
from asgiref.sync import sync_to_async
from django.db import connections

from apps.core import bus
from apps.core.evenements import publier, traiter_une_fois
from apps.core.models import EvenementSortant, EvenementTraite

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


def _sortants(cle):
    return EvenementSortant.objects.using("worker").filter(cle_idempotence=cle)


def test_une_publication_rejouee_avec_la_meme_cle_n_existe_qu_une_fois():
    cle = publier("campagne.terminee", donnees={"envoyes": 3})
    assert publier("campagne.terminee", cle_idempotence=cle) == cle
    assert _sortants(cle).count() == 1


def test_le_role_applicatif_ecrit_dans_la_boite_d_envoi_sans_pouvoir_la_lire():
    cle = publier("credit.seuil.franchi")
    assert not EvenementSortant.objects.filter(cle_idempotence=cle).exists()
    assert _sortants(cle).exists()


def test_un_evenement_traite_deux_fois_ne_produit_qu_un_traitement():
    appels = []
    assert traiter_une_fois("test", "cle-1", lambda: appels.append(1)) is True
    assert traiter_une_fois("test", "cle-1", lambda: appels.append(1)) is False
    assert traiter_une_fois("autre-consommateur", "cle-1", lambda: appels.append(1)) is True
    assert appels == [1, 1]


def test_un_traitement_qui_echoue_ne_laisse_pas_de_trace_et_pourra_etre_rejoue():
    def en_echec():
        raise RuntimeError("panne")

    with pytest.raises(RuntimeError):
        traiter_une_fois("test", "cle-2", en_echec)
    assert not EvenementTraite.objects.filter(cle_idempotence="cle-2").exists()
    appels = []
    assert traiter_une_fois("test", "cle-2", lambda: appels.append(1)) is True
    assert appels == [1]


def _executer(scenario):
    """Joue un scénario asynchrone, puis ferme les connexions ouvertes par le fil
    de `sync_to_async`, sans quoi la base de test ne peut pas être supprimée."""

    async def puis_fermer():
        try:
            return await scenario()
        finally:
            await sync_to_async(connections.close_all)()

    return asyncio.run(puis_fermer())


async def _file_de_test(canal, motif):
    echange = await bus.declarer_echange(canal)
    file = await canal.declare_queue(exclusive=True, auto_delete=True)
    await file.bind(echange, routing_key=motif)
    return file


def test_le_relais_publie_et_un_doublon_du_courtier_n_est_traite_qu_une_fois():
    sujet = f"test.{uuid.uuid4().hex}"
    cle = publier(f"{sujet}.cree", donnees={"n": 1})
    recus = []

    async def scenario():
        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel()
            file = await _file_de_test(canal, f"{sujet}.#")
            assert await bus.relayer(canal, using="worker") >= 1
            assert await bus.relayer(canal, using="worker") == 0
            # Doublon simulé : le même événement republié par le courtier.
            echange = await bus.declarer_echange(canal)
            corps = json.dumps({"nom": f"{sujet}.cree", "cle_idempotence": cle}).encode()
            await echange.publish(
                aio_pika.Message(corps, message_id=cle), routing_key=f"{sujet}.cree"
            )
            traites = []
            for _ in range(2):
                message = await file.get(timeout=5)
                traites.append(
                    await bus.traiter_message(message, "test-relais", recus.append, using="worker")
                )
            return traites

    assert _executer(scenario) == [True, False]
    assert len(recus) == 1 and recus[0]["donnees"] == {"n": 1}
    assert _sortants(cle).get().publie_le is not None


def test_un_consommateur_mort_apres_traitement_et_avant_acquittement_ne_double_pas():
    sujet = f"test.{uuid.uuid4().hex}"
    cle = publier(f"{sujet}.cree")
    recus = []

    async def scenario():
        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel()
            echange = await bus.declarer_echange(canal)
            file = await canal.declare_queue(f"test-{sujet}", durable=True)
            await file.bind(echange, routing_key=f"{sujet}.#")
            await bus.relayer(canal, using="worker")

            # Premier consommateur : traite et valide, puis meurt sans acquitter.
            canal_mort = await connexion.channel()
            file_morte = await canal_mort.get_queue(file.name)
            message = await file_morte.get(timeout=5)
            assert message.message_id == cle
            evenement = json.loads(message.body)
            await sync_to_async(traiter_une_fois)(
                "test-mort", message.message_id, lambda: recus.append(evenement)
            )
            await canal_mort.close()

            # Le courtier relivre le message au consommateur suivant.
            relivre = await file.get(timeout=5)
            traite = await bus.traiter_message(relivre, "test-mort", recus.append, using="worker")
            await file.delete(if_unused=False, if_empty=False)
            return relivre.redelivered, traite

    assert _executer(scenario) == (True, False)
    assert len(recus) == 1
