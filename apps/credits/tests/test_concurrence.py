"""Réservation sous concurrence, T-09.

Deux transactions réelles, sur deux connexions PostgreSQL distinctes (une par
fil d'exécution), tentent de réserver un solde qui n'en couvre qu'une.
"""

import threading
import time
import uuid

import pytest
from django.db import connection, connections, transaction

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.cloisonnement import poser_client
from apps.credits import services
from apps.credits.models import Reservation

pytestmark = pytest.mark.django_db(transaction=True)

SOLDE, DEMANDE = 1_000, 800


def _client_approvisionne():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    with transaction.atomic():
        poser_client(client.id)
        services.acheter(client.id, SOLDE, reference="OM-0001")
    return client


def _reserver(client_id, resultats, cle, avant_commit=None):
    try:
        with transaction.atomic():
            poser_client(client_id)
            reservation = services.reserver(client_id, DEMANDE, campagne_id=uuid.uuid7())
            if avant_commit:
                avant_commit()
        resultats[cle] = reservation
    except services.SoldeInsuffisant as erreur:
        resultats[cle] = erreur
    finally:
        connection.close()


def _solde(client_id):
    with transaction.atomic():
        poser_client(client_id)
        return services.solde(client_id), services.solde_recalcule(client_id)


def _attend_un_verrou(delai=5.0):
    """Vrai dès qu'une session du rôle applicatif attend un verrou. La lecture
    passe par ce même rôle : PostgreSQL masque l'état d'attente des sessions
    d'un autre rôle à qui n'est pas super-utilisateur."""
    fin = time.monotonic() + delai
    while time.monotonic() < fin:
        with connections["default"].cursor() as curseur:
            curseur.execute(
                "SELECT count(*) FROM pg_stat_activity"
                " WHERE datname = current_database() AND wait_event_type = 'Lock'"
            )
            if curseur.fetchone()[0]:
                return True
        time.sleep(0.02)
    return False


def test_la_seconde_reservation_attend_le_verrou_puis_est_refusee():
    client = _client_approvisionne()
    premiere_faite, relacher = threading.Event(), threading.Event()
    resultats = {}

    def tenir_la_transaction():
        premiere_faite.set()
        assert relacher.wait(10)

    premier = threading.Thread(
        target=_reserver, args=(client.id, resultats, "premier", tenir_la_transaction)
    )
    second = threading.Thread(target=_reserver, args=(client.id, resultats, "second"))
    premier.start()
    assert premiere_faite.wait(10)
    second.start()

    try:
        # Le second fil est bloqué sur SELECT ... FOR UPDATE tant que le premier n'a pas validé.
        assert _attend_un_verrou(), "la seconde réservation n'a pas attendu le verrou"
        assert "second" not in resultats
    finally:
        relacher.set()
        premier.join(10)
        second.join(10)

    assert isinstance(resultats["premier"], Reservation)
    assert isinstance(resultats["second"], services.SoldeInsuffisant)
    assert resultats["second"].disponible == SOLDE - DEMANDE
    cache, recalcule = _solde(client.id)
    assert cache == recalcule == services.Solde(SOLDE, DEMANDE)


@pytest.mark.parametrize("essai", range(5))
def test_deux_reservations_lancees_ensemble_une_seule_passe(essai):
    client = _client_approvisionne()
    depart = threading.Barrier(2)
    resultats = {}

    def lancer(cle):
        depart.wait(5)
        _reserver(client.id, resultats, cle)

    fils = [threading.Thread(target=lancer, args=(cle,)) for cle in ("a", "b")]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(10)

    refus = [r for r in resultats.values() if isinstance(r, services.SoldeInsuffisant)]
    assert len(resultats) == 2 and len(refus) == 1
    cache, recalcule = _solde(client.id)
    assert cache == recalcule == services.Solde(SOLDE, DEMANDE)
