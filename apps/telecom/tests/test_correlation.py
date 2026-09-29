"""Corrélation des accusés, T-14. Contre le vrai Redis, sous le rôle worker."""

import uuid

import pytest
from django.db import transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.journal.models import Etat, Evenement, Message
from apps.journal.services import appliquer_accuse, enregistrer_message, soumettre
from apps.telecom import correlation
from apps.telecom.models import AccuseOrphelin, Connecteur, Correlation, Operateur
from apps.telecom.passerelles import Accuse, passerelle_pour

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def passerelle(en_tant_que_worker):
    connecteur = Connecteur.objects.create(
        nom=f"simulateur-{timezone.now().timestamp()}",
        operateur=Operateur.MOOV,
        type=Connecteur.Type.SIMULE,
        parametres={"taux_echec": 0},
    )
    return passerelle_pour(connecteur)


@pytest.fixture
def message_soumis(passerelle):
    client = creer_client(
        raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    message = enregistrer_message(
        client=client,
        numero_normalise="+22660123456",
        operateur="MOOV",
        expediteur_libelle="EXEMPLE",
        texte="Bonjour",
        cout_unitaire=10,
    )
    message = soumettre(message, passerelle)
    yield message
    correlation._redis().delete(
        correlation.cle_redis(passerelle.connecteur.id, message.reference_smsc)
    )


def test_la_correspondance_est_en_base_et_dans_redis_pour_72_heures(passerelle, message_soumis):
    cle = correlation.cle_redis(passerelle.connecteur.id, message_soumis.reference_smsc)
    duree = correlation._redis().ttl(cle)
    assert 71 * 3600 < duree <= 72 * 3600
    assert Correlation.objects.filter(reference_smsc=message_soumis.reference_smsc).exists()


def test_un_accuse_normal_est_rapproche_de_son_message(passerelle, message_soumis):
    accuse = passerelle.accuses_dus()[0]
    assert appliquer_accuse(accuse) is True
    assert Message.objects.get(id=message_soumis.id).etat == Etat.LIVRE


def test_un_accuse_arrive_apres_expiration_de_la_cle_redis_est_retrouve_en_base(
    passerelle, message_soumis
):
    correlation._redis().delete(
        correlation.cle_redis(passerelle.connecteur.id, message_soumis.reference_smsc)
    )
    accuse = passerelle.accuses_dus()[0]
    assert appliquer_accuse(accuse) is True
    assert Message.objects.get(id=message_soumis.id).etat == Etat.LIVRE


def test_un_accuse_sans_correspondance_est_range_parmi_les_orphelins(passerelle):
    accuse = Accuse(
        connecteur_id=passerelle.connecteur.id,
        reference_smsc="ffff0000deadbeef",
        libelle_operateur="DELIVRD",
        etat=Etat.LIVRE,
        horodatage=timezone.now(),
    )
    assert appliquer_accuse(accuse) is False
    orphelin = AccuseOrphelin.objects.get(reference_smsc="ffff0000deadbeef")
    assert orphelin.charge_utile["libelle_operateur"] == "DELIVRD"
    assert orphelin.connecteur_id == passerelle.connecteur.id


def test_un_accuse_rejoue_deux_fois_ne_produit_qu_une_transition(passerelle, message_soumis):
    accuse = passerelle.accuses_dus()[0]
    assert [appliquer_accuse(accuse) for _ in range(3)] == [True, False, False]
    assert Evenement.objects.filter(message_id=message_soumis.id, etat=Etat.LIVRE).count() == 1
    assert not AccuseOrphelin.objects.exists()


def test_deux_operateurs_peuvent_rendre_le_meme_identifiant_sans_se_meler(passerelle):
    autre = Connecteur.objects.create(
        nom="autre", operateur=Operateur.ORANGE, type=Connecteur.Type.SIMULE
    )
    premier, second, maintenant = uuid.uuid7(), uuid.uuid7(), timezone.now()
    with transaction.atomic():
        correlation.enregistrer(passerelle.connecteur.id, "00000001", premier, maintenant)
        correlation.enregistrer(autre.id, "00000001", second, maintenant)
    try:
        for connecteur_id, attendu in ((passerelle.connecteur.id, premier), (autre.id, second)):
            trouve = correlation.retrouver(connecteur_id, "00000001")
            assert str(trouve.message_id) == str(attendu)
            assert trouve.message_date_reception == maintenant
    finally:
        for connecteur_id in (passerelle.connecteur.id, autre.id):
            correlation._redis().delete(correlation.cle_redis(connecteur_id, "00000001"))


def test_une_soumission_annulee_ne_laisse_rien_dans_redis(passerelle):
    cle = correlation.cle_redis(passerelle.connecteur.id, "annulee")
    with pytest.raises(RuntimeError), transaction.atomic():
        correlation.enregistrer(passerelle.connecteur.id, "annulee", uuid.uuid7(), timezone.now())
        raise RuntimeError("panne avant validation")
    assert correlation._redis().get(cle) is None
    assert not Correlation.objects.filter(reference_smsc="annulee").exists()


def test_la_correspondance_vit_la_validite_du_sms_plus_la_marge(passerelle, settings):
    settings.VALIDITE_SMS_HEURES = 24
    maintenant, message_id = timezone.now(), uuid.uuid7()
    with transaction.atomic():
        correlation.enregistrer(passerelle.connecteur.id, "courte", message_id, maintenant)
    try:
        duree = correlation._redis().ttl(correlation.cle_redis(passerelle.connecteur.id, "courte"))
        assert 47 * 3600 < duree <= 48 * 3600  # 24 heures de validité, 24 de marge
        ligne = Correlation.objects.get(reference_smsc="courte")
        ecart = (ligne.expire_le - ligne.cree_le).total_seconds()
        assert abs(ecart - 48 * 3600) < 60
    finally:
        correlation._redis().delete(correlation.cle_redis(passerelle.connecteur.id, "courte"))
