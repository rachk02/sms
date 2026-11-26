"""Unicité de l'écriture ACHAT d'un paiement (décision B1 du 9 novembre 2026).

Les paiements en ligne et manuels portent la même référence
`paiement:<transaction>`. La base refuse un second achat sous cette référence,
même si le service qui la pose est contourné ou rejoué en parallèle.
"""

import threading
import uuid

import pytest
from django.db import IntegrityError, connection, transaction

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.cloisonnement import poser_client
from apps.credits import services
from apps.credits.models import Ecriture

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def client_exemple():
    return creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)


def _acheter(client_id, montant, reference):
    with transaction.atomic():
        poser_client(client_id)
        return services.acheter(client_id, montant, reference=reference)


def _achats(client_id, reference):
    with transaction.atomic():
        poser_client(client_id)
        return list(Ecriture.objects.filter(type="ACHAT", reference=reference))


def test_un_second_achat_pour_le_meme_paiement_est_refuse_par_la_base(client_exemple):
    reference = f"paiement:{uuid.uuid7()}"
    _acheter(client_exemple.id, 1_000, reference)
    with pytest.raises(IntegrityError):
        _acheter(client_exemple.id, 1_000, reference)
    assert [a.montant for a in _achats(client_exemple.id, reference)] == [1_000]


def test_deux_achats_simultanes_du_meme_paiement_n_en_ecrivent_qu_un(client_exemple):
    reference = f"paiement:{uuid.uuid7()}"
    depart, resultats = threading.Barrier(2), []

    def acheter():
        try:
            depart.wait(5)
            _acheter(client_exemple.id, 1_000, reference)
            resultats.append("ecrit")
        except IntegrityError:
            resultats.append("refuse")
        finally:
            connection.close()

    fils = [threading.Thread(target=acheter) for _ in range(2)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(30)

    assert sorted(resultats) == ["ecrit", "refuse"]
    assert len(_achats(client_exemple.id, reference)) == 1
    with transaction.atomic():
        poser_client(client_exemple.id)
        assert services.solde(client_exemple.id) == services.solde_recalcule(client_exemple.id)


def test_les_autres_formes_de_reference_ne_sont_pas_touchees(client_exemple):
    """Un virement saisi sous sa propre référence reste libre : seule la forme
    `paiement:` porte la contrainte."""
    _acheter(client_exemple.id, 1_000, "VIR-2026-001")
    _acheter(client_exemple.id, 500, "VIR-2026-001")
    assert [a.montant for a in _achats(client_exemple.id, "VIR-2026-001")] == [1_000, 500]
