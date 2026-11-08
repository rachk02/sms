"""Réservation groupée d'un lot de l'API (T-108).

Mêmes règles que la réservation unitaire, ligne par ligne : les premières
lignes passent tant que le disponible les couvre, une réservation par message,
et deux lots lancés en même temps ne réservent jamais plus que le solde.
"""

import threading
import uuid

import pytest
from django.db import connection, transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.cloisonnement import poser_client
from apps.credits import services
from apps.credits.models import Reservation

pytestmark = pytest.mark.django_db(transaction=True)

SOLDE = 1_000


def _client(solde=SOLDE):
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    with transaction.atomic():
        poser_client(client.id)
        services.acheter(client.id, solde, reference=f"OM-{uuid.uuid4().hex[:6]}")
    return client


def _lignes(nombre, montant):
    maintenant = timezone.now()
    return [services.LigneReservation(uuid.uuid7(), maintenant, montant) for _ in range(nombre)]


def test_les_premieres_lignes_passent_tant_que_le_disponible_suffit():
    client = _client()
    lignes = _lignes(4, 300)
    with transaction.atomic():
        poser_client(client.id)
        assert services.reserver_messages(client.id, lignes) == 3
        reservations = Reservation.objects.filter(client_id=client.id)
        assert sorted(str(r.message_id) for r in reservations) == sorted(
            str(ligne.message_id) for ligne in lignes[:3]
        )
        assert {r.mode for r in reservations} == {Client.Reglement.SOUMISSION}
        solde = services.solde(client.id)
        assert solde.reserve == 900 and solde.disponible == 100
        assert services.solde_recalcule(client.id) == solde


def test_une_ligne_qui_ne_passe_pas_arrete_le_lot():
    # La deuxième ligne ne tient pas : la troisième, plus petite, ne passe pas
    # devant elle.
    client = _client()
    lignes = [*_lignes(1, 600), *_lignes(1, 600), *_lignes(1, 100)]
    with transaction.atomic():
        poser_client(client.id)
        assert services.reserver_messages(client.id, lignes) == 1
        assert services.solde(client.id).reserve == 600


def test_deux_lots_simultanes_ne_reservent_jamais_plus_que_le_solde():
    client = _client()
    depart = threading.Barrier(2)
    resultats = {}

    def lot(cle):
        try:
            depart.wait(5)
            with transaction.atomic():
                poser_client(client.id)
                resultats[cle] = services.reserver_messages(client.id, _lignes(7, 100))
        finally:
            connection.close()

    fils = [threading.Thread(target=lot, args=(cle,)) for cle in ("a", "b")]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(15)
    # Quatorze messages de 100 contre un solde de 1 000 : dix passent, pas un de plus.
    assert sorted(resultats.values()) == [3, 7]
    with transaction.atomic():
        poser_client(client.id)
        solde = services.solde(client.id)
        assert solde.reserve == SOLDE and solde.disponible == 0
        assert Reservation.objects.filter(client_id=client.id).count() == 10
        assert services.solde_recalcule(client.id) == solde
