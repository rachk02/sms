"""Traitement des accusés et transitions, T-27."""

import asyncio
import logging
import time
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core import bus
from apps.core.etats import ETATS_TERMINAUX, TRANSITIONS, Etat, transition_permise
from apps.core.files import Topologie
from apps.journal.models import Evenement, Message
from apps.journal.services import (
    TransitionInterdite,
    appliquer_accuse,
    changer_etat,
    enregistrer_message,
    soumettre,
)
from apps.telecom import accuses, correlation
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import Accuse, passerelle_pour
from workers.accuses import WorkerAccuses

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def passerelle(en_tant_que_worker):
    connecteur = Connecteur.objects.create(
        nom=f"c-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SIMULE,
        parametres={"taux_echec": 0},
    )
    return passerelle_pour(connecteur)


@pytest.fixture
def soumis(passerelle):
    client = creer_client(
        raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    message = enregistrer_message(
        client=client,
        numero_normalise="+22670123456",
        operateur="ORANGE",
        expediteur_libelle="EXEMPLE",
        texte="Bonjour",
        cout_unitaire=10,
    )
    message = soumettre(message, passerelle)
    yield message
    correlation._redis().delete(
        correlation.cle_redis(passerelle.connecteur.id, message.reference_smsc)
    )


def test_un_etat_terminal_n_a_aucune_sortie():
    for etat in ETATS_TERMINAUX:
        assert TRANSITIONS[etat] == frozenset()
    assert set(TRANSITIONS) == set(Etat.values)
    assert transition_permise(Etat.SOUMIS, Etat.LIVRE)
    assert not transition_permise(Etat.LIVRE, Etat.EN_FILE)


def test_une_transition_interdite_est_refusee_et_journalisee(passerelle, soumis, caplog):
    appliquer_accuse(passerelle.accuses_dus()[0])
    livre = Message.objects.get(id=soumis.id)
    evenements = Evenement.objects.filter(message_id=soumis.id).count()

    with caplog.at_level(logging.WARNING, logger="apps.journal.services"):
        with pytest.raises(TransitionInterdite):
            changer_etat(livre, Etat.EN_FILE, source=Evenement.Source.ADMIN)
    assert "LIVRE vers EN_FILE" in caplog.text
    assert Message.objects.get(id=soumis.id).etat == Etat.LIVRE
    assert Evenement.objects.filter(message_id=soumis.id).count() == evenements


def test_un_message_livre_porte_trois_evenements_dates_dans_l_ordre(passerelle, soumis):
    appliquer_accuse(passerelle.accuses_dus()[0])
    historique = list(Evenement.objects.filter(message_id=soumis.id).order_by("horodatage", "id"))
    assert [e.etat for e in historique] == [Etat.RECU, Etat.SOUMIS, Etat.LIVRE]
    assert [e.source for e in historique] == ["API", "WORKER", "ACCUSE"]
    horodatages = [e.horodatage for e in historique]
    assert horodatages == sorted(horodatages)
    assert historique[1].details["reference_smsc"] == soumis.reference_smsc


def test_un_accuse_contradictoire_apres_l_etat_final_est_refuse(passerelle, soumis, caplog):
    accuse = passerelle.accuses_dus()[0]
    assert appliquer_accuse(accuse) is True
    contradictoire = Accuse(
        accuse.connecteur_id, accuse.reference_smsc, "UNDELIV", Etat.NON_LIVRE, timezone.now()
    )
    with caplog.at_level(logging.WARNING, logger="apps.journal.services"):
        assert appliquer_accuse(contradictoire) is False
    assert "LIVRE vers NON_LIVRE" in caplog.text
    assert Message.objects.get(id=soumis.id).etat == Etat.LIVRE


def test_le_worker_accuses_applique_les_accuses_de_sa_file(passerelle, soumis):
    topologie = Topologie(prefixe=f"test-{uuid.uuid4().hex[:8]}.", paliers_ms=(200,) * 5)
    worker = WorkerAccuses(topologie)
    accuse = passerelle.accuses_dus()[0]

    async def scenario():
        execution = asyncio.create_task(worker.executer(installer_signaux=False))
        await asyncio.wait_for(worker.pret.wait(), 10)
        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel()
            await accuses.publier(canal, accuse, topologie)
            await accuses.publier(canal, accuse, topologie)  # relivraison : sans effet
            fin = time.monotonic() + 10
            while time.monotonic() < fin:
                etat = await sync_to_async(lambda: Message.objects.get(id=soumis.id).etat)()
                if etat == Etat.LIVRE:
                    break
                await asyncio.sleep(0.2)
            await asyncio.sleep(0.5)
            worker.arret.set()
            await asyncio.wait_for(execution, 15)
            for base in topologie.files:
                await canal.queue_delete(topologie.nom(base))
            for palier in range(1, 6):
                await canal.queue_delete(topologie.file_palier(palier))
            await canal.exchange_delete(topologie.echange)
            await canal.exchange_delete(topologie.echange_reessai)

    asyncio.run(scenario())
    assert Message.objects.get(id=soumis.id).etat == Etat.LIVRE
    assert Evenement.objects.filter(message_id=soumis.id, etat=Etat.LIVRE).count() == 1
