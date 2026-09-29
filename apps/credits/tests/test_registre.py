"""Registre de crédits, T-08."""

import uuid

import pytest
from django.db import IntegrityError, ProgrammingError, connections, transaction

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.cloisonnement import poser_client
from apps.credits import services
from apps.credits.models import CompteCredit, Ecriture, EcritureSeuleError, Reservation

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    poser_client(client.id)
    return client


@pytest.fixture
def agent():
    return creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x-1234-abcd", nom="A")


@pytest.fixture
def ecriture(client):
    return services.acheter(client.id, 10_000, reference="OM-0001")


def test_une_ecriture_ne_se_modifie_pas_par_le_modele(ecriture):
    ecriture.montant = 1
    with pytest.raises(EcritureSeuleError):
        ecriture.save()


def test_une_ecriture_ne_se_supprime_pas_par_le_modele(ecriture):
    with pytest.raises(EcritureSeuleError):
        ecriture.delete()


def test_une_ecriture_ne_se_modifie_ni_ne_se_supprime_en_masse(ecriture):
    with pytest.raises(EcritureSeuleError):
        Ecriture.objects.filter(id=ecriture.id).update(montant=1)
    with pytest.raises(EcritureSeuleError):
        Ecriture.objects.filter(id=ecriture.id).delete()
    with pytest.raises(EcritureSeuleError):
        Ecriture.objects.bulk_update([ecriture], ["montant"])


@pytest.mark.parametrize(
    "sql", ["UPDATE credits_registre SET montant = 1", "DELETE FROM credits_registre"]
)
def test_la_base_refuse_toute_modification_ou_suppression_du_registre(ecriture, sql):
    with pytest.raises(ProgrammingError, match="écriture seule"), transaction.atomic():
        with connections["default"].cursor() as curseur:
            curseur.execute(sql)
    assert Ecriture.objects.get(id=ecriture.id).montant == 10_000


def test_le_solde_recalcule_egale_le_cache_apres_une_serie_d_operations(client, agent):
    services.acheter(client.id, 10_000, reference="OM-0001")
    campagne = services.reserver(client.id, 3_000, campagne_id=uuid.uuid7())
    services.regler(campagne.id, 1_000, reference="lot-1")
    services.regler(campagne.id, 500, reference="lot-2")
    assert services.liberer(campagne.id) == 1_500
    services.recrediter(client.id, 200, motif="Panne de liaison du 12", auteur=agent)
    services.ajuster(client.id, -150, motif="Doublon de recrédit", auteur=agent)
    unitaire = services.reserver(client.id, 2_000, message_id=uuid.uuid7())
    services.regler(unitaire.id, 2_000)
    services.reserver(client.id, 700, message_id=uuid.uuid7())

    cache = services.solde(client.id)
    assert cache == services.solde_recalcule(client.id)
    assert (cache.acquis, cache.reserve) == (10_000 - 1_500 + 200 - 150 - 2_000, 700)
    assert cache.disponible == 5_850
    derniere = Ecriture.objects.filter(client_id=client.id).order_by("-cree_le", "-id").first()
    assert derniere.solde_apres == cache.acquis


def test_un_ajustement_sans_motif_est_refuse(client, agent):
    for motif in ("", "   "):
        with pytest.raises(services.MotifObligatoire):
            services.ajuster(client.id, 100, motif=motif, auteur=agent)
    with pytest.raises(services.MotifObligatoire):
        services.ajuster(client.id, 100, motif="Correction", auteur=None)
    assert not Ecriture.objects.filter(client_id=client.id).exists()


def test_la_base_refuse_aussi_un_ajustement_sans_motif(client):
    with pytest.raises(IntegrityError), transaction.atomic():
        Ecriture(
            client_id=client.id, type=Ecriture.Type.AJUSTEMENT, montant=100, solde_apres=100
        ).save()


@pytest.mark.parametrize(
    ("type_", "montant"),
    [("ACHAT", -100), ("RECREDIT", 0), ("CONSOMMATION", 100), ("AJUSTEMENT", 0)],
)
def test_la_base_refuse_un_montant_de_mauvais_signe(client, type_, montant):
    with pytest.raises(IntegrityError), transaction.atomic():
        Ecriture(client_id=client.id, type=type_, montant=montant, solde_apres=0, motif="x").save()


def test_une_reservation_au_dela_du_disponible_est_refusee(client):
    services.acheter(client.id, 1_000, reference="OM-0002")
    services.reserver(client.id, 800, campagne_id=uuid.uuid7())
    with pytest.raises(services.SoldeInsuffisant) as erreur:
        services.reserver(client.id, 201, campagne_id=uuid.uuid7())
    assert (erreur.value.disponible, erreur.value.demande) == (200, 201)
    assert services.solde(client.id).reserve == 800


def test_on_ne_regle_pas_plus_que_le_reste_ni_une_reservation_close(client):
    services.acheter(client.id, 1_000, reference="OM-0003")
    reservation = services.reserver(client.id, 500, message_id=uuid.uuid7())
    with pytest.raises(ValueError):
        services.regler(reservation.id, 501)
    services.regler(reservation.id, 500)
    assert Reservation.objects.get(id=reservation.id).etat == Reservation.Etat.REGLEE
    with pytest.raises(services.ReservationClose):
        services.liberer(reservation.id)


def test_une_reservation_porte_une_campagne_ou_un_message_jamais_les_deux(client):
    services.acheter(client.id, 1_000, reference="OM-0004")
    with pytest.raises(IntegrityError), transaction.atomic():
        services.reserver(client.id, 100, campagne_id=uuid.uuid7(), message_id=uuid.uuid7())
    with pytest.raises(IntegrityError), transaction.atomic():
        services.reserver(client.id, 100)


def test_un_montant_non_entier_ou_negatif_est_refuse(client):
    for montant in (0, -5, 12.5, "100"):
        with pytest.raises(ValueError):
            services.acheter(client.id, montant, reference="x")


def test_un_autre_client_ne_voit_ni_le_registre_ni_le_solde(client, ecriture):
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    poser_client(autre.id)
    assert not Ecriture.objects.exists()
    assert not CompteCredit.objects.exists()
    assert services.solde(client.id) == services.Solde(0, 0)
