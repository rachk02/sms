"""Débit par réseau et priorité du transactionnel (T-63).

Contre le vrai RabbitMQ et le vrai Redis, avec un connecteur simulé : le débit
réglé à dix messages par seconde en produit dix et pas davantage, et un envoi
transactionnel passe devant une campagne de quarante mille messages en cours.
"""

import asyncio
import json
import time
import uuid
from types import SimpleNamespace

import aio_pika
import pytest
from asgiref.sync import sync_to_async
from django.conf import settings
from django.db import connection
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core import bus
from apps.core.files import Topologie
from apps.journal.models import Message
from apps.journal.services import enregistrer_message
from apps.telecom import debit
from apps.telecom.models import Connecteur
from workers.envoi import WorkerEnvoi

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


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


@pytest.fixture
def client(en_tant_que_worker):
    return creer_client(
        raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )


@pytest.fixture
def connecteur(client):
    return Connecteur.objects.create(
        nom=f"simule-{uuid.uuid4().hex[:6]}",
        operateur="ORANGE",
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        debit_max=10,
    )


def _corps(message) -> bytes:
    return json.dumps(
        {
            "donnees": {
                "message_id": str(message.id),
                "date_reception": message.date_reception.isoformat(),
            }
        }
    ).encode()


def _message(client):
    return enregistrer_message(
        client=client,
        numero_normalise="+22670123456",
        operateur="ORANGE",
        expediteur_libelle=settings.SMS_NUMERO_COURT,
        texte="Promotion",
        cout_unitaire=10,
    )


def test_les_creneaux_d_un_connecteur_sont_espaces_de_un_sur_le_debit():
    connecteur = SimpleNamespace(id=uuid.uuid4(), debit_max=10)
    autre = SimpleNamespace(id=uuid.uuid4(), debit_max=10)
    attentes = [debit.creneau(connecteur) for _ in range(5)]
    for rang, attente in enumerate(attentes):
        assert attente == pytest.approx(rang * 0.103, abs=0.03)
    assert debit.creneau(autre) == pytest.approx(0, abs=0.03)  # chaque connecteur a le sien


def _max_par_seconde(instants: list[float]) -> int:
    instants = sorted(instants)
    return max(sum(1 for t in instants[i:] if t < debut + 1.0) for i, debut in enumerate(instants))


def test_un_debit_de_dix_par_seconde_en_produit_dix_et_pas_davantage(client, connecteur):
    topologie = _topologie()
    messages = [_message(client) for _ in range(35)]
    worker = WorkerEnvoi(topologie, files=["envoi.masse.orange"], prefetch=10)

    async def scenario():
        execution = asyncio.create_task(worker.executer(installer_signaux=False))
        await asyncio.wait_for(worker.pret.wait(), 10)
        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel()
            echange = await canal.get_exchange(topologie.echange)
            for message in messages:
                await echange.publish(
                    aio_pika.Message(_corps(message)),
                    routing_key=topologie.nom("envoi.masse.orange"),
                )
            fin = time.monotonic() + 15
            while time.monotonic() < fin:
                soumis = await sync_to_async(
                    Message.objects.filter(
                        id__in=[m.id for m in messages], date_soumission__isnull=False
                    ).count
                )()
                if soumis == len(messages):
                    break
                await asyncio.sleep(0.2)
        worker.arret.set()
        await asyncio.wait_for(execution, 10)

    try:
        asyncio.run(scenario())
    finally:
        asyncio.run(_nettoyer(topologie))
    # L'instant où la plateforme lance chaque soumission, après son créneau.
    instants = [
        d.timestamp()
        for d in Message.objects.filter(id__in=[m.id for m in messages]).values_list(
            "soumission_debutee_le", flat=True
        )
    ]
    assert len(instants) == 35 and all(instants)
    duree = max(instants) - min(instants)
    print(f"35 messages soumis en {duree:.2f} s, au plus {_max_par_seconde(instants)} par seconde")
    assert _max_par_seconde(instants) <= 10
    # Dix par seconde moins la marge de 3 % : ni plus, ni sensiblement moins.
    assert 3.4 <= duree <= 3.9


QUARANTE_MILLE = 40_000


def _arriere(client) -> list[tuple]:
    """Quarante mille messages de campagne déjà au journal, écrits d'un coup."""
    with connection.cursor() as curseur:
        curseur.execute(
            """
            INSERT INTO journal_message (
                id, date_reception, client_id, numero_normalise, operateur, expediteur_libelle,
                texte, encodage, segments, cout_unitaire, cout_total, etat, motif_echec,
                reference_smsc, cle_idempotence, modifie_le
            )
            SELECT gen_random_uuid(), now(), %s, '+2267' || lpad(n::text, 7, '0'), 'ORANGE',
                   %s, 'Promotion', 'GSM7', 1, 10, 10, 'RECU', '', '', '', now()
            FROM generate_series(1, %s) AS n
            RETURNING id, date_reception
            """,
            [client.id, settings.SMS_NUMERO_COURT, QUARANTE_MILLE],
        )
        return curseur.fetchall()


def test_un_envoi_transactionnel_passe_devant_une_campagne_de_quarante_mille(client, connecteur):
    topologie = _topologie()
    arriere = _arriere(client)
    code = _message(client)
    masse = WorkerEnvoi(topologie, files=["envoi.masse.orange"], prefetch=10)
    prioritaire = WorkerEnvoi(topologie, files=["envoi.prioritaire.orange"], prefetch=10)

    async def scenario():
        taches = [
            asyncio.create_task(masse.executer(installer_signaux=False)),
            asyncio.create_task(prioritaire.executer(installer_signaux=False)),
        ]
        await asyncio.wait_for(masse.pret.wait(), 10)
        await asyncio.wait_for(prioritaire.pret.wait(), 10)
        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel(publisher_confirms=False)
            echange = await canal.get_exchange(topologie.echange)
            for identifiant, date_reception in arriere:
                corps = json.dumps(
                    {"message_id": str(identifiant), "date_reception": date_reception.isoformat()}
                ).encode()
                await echange.publish(
                    aio_pika.Message(corps), routing_key=topologie.nom("envoi.masse.orange")
                )
            await asyncio.sleep(2)  # la campagne est en cours de diffusion
            depart = timezone.now()
            await echange.publish(
                aio_pika.Message(_corps(code)),
                routing_key=topologie.nom("envoi.prioritaire.orange"),
            )
            fin = time.monotonic() + 10
            while time.monotonic() < fin:
                await sync_to_async(code.refresh_from_db)()
                if code.date_soumission:
                    break
                await asyncio.sleep(0.05)
            file_masse = await canal.declare_queue(
                topologie.nom("envoi.masse.orange"), passive=True
            )
            restant = file_masse.declaration_result.message_count
        masse.arret.set()
        prioritaire.arret.set()
        await asyncio.wait_for(asyncio.gather(*taches), 15)
        return depart, restant

    try:
        depart, restant = asyncio.run(scenario())
    finally:
        asyncio.run(_nettoyer(topologie))
    assert code.date_soumission is not None, "le code n'est pas parti"
    delai_code = (code.date_soumission - depart).total_seconds()
    delai_masse = restant / connecteur.debit_max  # attente du dernier message de la campagne
    print(f"code soumis en {delai_code:.2f} s ; {restant} messages de campagne encore en file")
    assert delai_code < 2.0
    assert restant > QUARANTE_MILLE - 200
    assert delai_masse > 1_000 * delai_code
