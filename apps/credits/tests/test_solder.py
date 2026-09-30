"""Consommation à la soumission, jusqu'au lot 7 (décision du 30 septembre 2026).

Un message soumis consomme son coût une seule fois, sur la réservation de sa
campagne ou sur la sienne ; un refus ne coûte rien. Rejeu et concurrence sont
vérifiés sur des transactions réelles, comme pour la réservation (T-09).
"""

import threading
import uuid

import pytest
from django.db import IntegrityError, connection, transaction

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.credits import services
from apps.credits.models import Ecriture, Reservation

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def client(en_tant_que_worker):
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    services.acheter(client.id, 10_000, reference="OM-0001")
    return client


def _consommations(client):
    return Ecriture.objects.filter(client_id=client.id, type=Ecriture.Type.CONSOMMATION)


def test_un_message_de_campagne_consomme_sur_la_reservation_de_la_campagne(client):
    campagne_id = uuid.uuid7()
    reservation = services.reserver(client.id, 1_000, campagne_id=campagne_id)
    for _ in range(3):
        services.solder_message(
            client_id=client.id,
            message_id=uuid.uuid7(),
            campagne_id=campagne_id,
            montant=40,
            soumis=True,
        )
    services.solder_message(
        client_id=client.id,
        message_id=uuid.uuid7(),
        campagne_id=campagne_id,
        montant=40,
        soumis=False,
    )
    reservation.refresh_from_db()
    assert reservation.montant_restant == 1_000 - 120 and reservation.etat == "ACTIVE"
    assert services.solde(client.id) == services.solde_recalcule(client.id)
    assert services.solde(client.id).acquis == 10_000 - 120


def test_un_envoi_hors_campagne_consomme_ou_libere_sa_propre_reservation(client):
    soumis, refuse = uuid.uuid7(), uuid.uuid7()
    services.reserver(client.id, 60, message_id=soumis)
    services.reserver(client.id, 60, message_id=refuse)
    services.solder_message(client_id=client.id, message_id=soumis, montant=60, soumis=True)
    services.solder_message(client_id=client.id, message_id=refuse, montant=60, soumis=False)
    etats = dict(Reservation.objects.values_list("message_id", "etat"))
    assert etats == {soumis: "REGLEE", refuse: "LIBEREE"}
    assert services.solde(client.id).disponible == 10_000 - 60


def test_une_soumission_rejouee_ne_consomme_qu_une_fois(client):
    campagne_id, message_id = uuid.uuid7(), uuid.uuid7()
    services.reserver(client.id, 1_000, campagne_id=campagne_id)
    premiere = services.solder_message(
        client_id=client.id, message_id=message_id, campagne_id=campagne_id, montant=40, soumis=True
    )
    rejouee = services.solder_message(
        client_id=client.id, message_id=message_id, campagne_id=campagne_id, montant=40, soumis=True
    )
    assert premiere is not None and rejouee is None
    assert _consommations(client).count() == 1


def test_la_base_refuse_une_seconde_consommation_du_meme_message(client):
    message_id = uuid.uuid7()
    services.reserver(client.id, 100, message_id=message_id)
    services.solder_message(client_id=client.id, message_id=message_id, montant=40, soumis=True)
    with pytest.raises(IntegrityError), transaction.atomic():
        Ecriture.objects.create(
            client_id=client.id,
            type=Ecriture.Type.CONSOMMATION,
            montant=-40,
            solde_apres=0,
            reference=services.reference_message(message_id),
        )


def test_deux_workers_qui_soldent_le_meme_message_en_meme_temps_ne_consomment_qu_une_fois(client):
    campagne_id, message_id = uuid.uuid7(), uuid.uuid7()
    services.reserver(client.id, 1_000, campagne_id=campagne_id)
    depart, erreurs = threading.Barrier(2), []

    def solder():
        try:
            depart.wait()
            services.solder_message(
                client_id=client.id,
                message_id=message_id,
                campagne_id=campagne_id,
                montant=40,
                soumis=True,
            )
        except Exception as erreur:  # noqa: BLE001 - remonté par l'assertion ci-dessous
            erreurs.append(erreur)
        finally:
            connection.close()

    fils = [threading.Thread(target=solder) for _ in range(2)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join()
    assert not erreurs
    assert _consommations(client).count() == 1
    assert services.solde(client.id) == services.solde_recalcule(client.id)


def test_un_message_sans_reservation_est_signale(client):
    with pytest.raises(services.SansReservation):
        services.solder_message(
            client_id=client.id, message_id=uuid.uuid7(), montant=40, soumis=True
        )

def test_une_campagne_ne_consomme_jamais_plus_que_sa_reservation(client):
    """Un message plus long que prévu (contact modifié depuis la confirmation)
    est facturé dans la limite de ce qui reste réservé : le coût réel d'une
    campagne ne dépasse jamais le coût annoncé."""
    campagne_id = uuid.uuid7()
    reservation = services.reserver(client.id, 100, campagne_id=campagne_id)
    for montant in (40, 40, 40, 40):
        services.solder_message(
            client_id=client.id,
            message_id=uuid.uuid7(),
            campagne_id=campagne_id,
            montant=montant,
            soumis=True,
        )
    reservation.refresh_from_db()
    assert (reservation.montant_restant, reservation.etat) == (0, "REGLEE")
    assert sum(_consommations(client).values_list("montant", flat=True)) == -100
    assert services.solde(client.id) == services.solde_recalcule(client.id)
