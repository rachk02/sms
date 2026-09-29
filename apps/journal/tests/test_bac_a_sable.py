"""Bac à sable et paliers de compte, T-40."""

from datetime import timedelta

import pytest
from django.utils import timezone

from apps.comptes.models import Client, NumeroTest
from apps.comptes.services import (
    NumeroTestRefuse,
    creer_client,
    declarer_numero_test,
    verifier_numero_test,
)
from apps.core.etats import Etat
from apps.journal.services import MOTIF_BAC_A_SABLE, enregistrer_message, soumettre
from apps.telecom import correlation
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import passerelle_pour

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def passerelle(en_tant_que_worker):
    connecteur = Connecteur.objects.create(
        nom=f"c-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SIMULE,
    )
    return passerelle_pour(connecteur)


def _envoyer(client, passerelle, numero):
    message = enregistrer_message(
        client=client,
        numero_normalise=numero,
        operateur="ORANGE",
        expediteur_libelle="EXEMPLE",
        texte="Bonjour",
        cout_unitaire=10,
    )
    message = soumettre(message, passerelle)
    if message.reference_smsc:
        correlation._redis().delete(
            correlation.cle_redis(passerelle.connecteur.id, message.reference_smsc)
        )
    return message


def test_un_compte_en_bac_a_sable_n_envoie_pas_a_un_numero_non_verifie(passerelle):
    client = creer_client(raison_sociale="Découverte SA", forme=Client.Forme.SOCIETE)
    assert client.palier == Client.Palier.DECOUVERTE
    message = _envoyer(client, passerelle, "+22670123456")
    assert (message.etat, message.motif_echec) == (Etat.REJETE, MOTIF_BAC_A_SABLE)
    assert passerelle.soumissions == {}, "le refus doit précéder la passerelle"


def test_un_compte_en_bac_a_sable_envoie_a_ses_numeros_de_test_verifies(passerelle):
    client = creer_client(raison_sociale="Découverte SA", forme=Client.Forme.SOCIETE)
    _, code = declarer_numero_test(client, "+22670123456")
    assert _envoyer(client, passerelle, "+22670123456").etat == Etat.REJETE  # pas encore vérifié
    assert verifier_numero_test(client, "+22670123456", code) is True
    assert _envoyer(client, passerelle, "+22670123456").etat == Etat.SOUMIS


def test_un_compte_verifie_envoie_a_tout_numero(passerelle):
    client = creer_client(
        raison_sociale="Vérifié SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    assert _envoyer(client, passerelle, "+22670999999").etat == Etat.SOUMIS


def test_un_code_faux_ou_expire_ne_verifie_pas_le_numero(passerelle):
    client = creer_client(raison_sociale="Découverte SA", forme=Client.Forme.SOCIETE)
    numero, code = declarer_numero_test(client, "+22670123456")
    faux = f"{(int(code) + 1) % 1_000_000:06d}"
    assert verifier_numero_test(client, "+22670123456", faux) is False
    NumeroTest.objects.filter(id=numero.id).update(
        code_expire_le=timezone.now() - timedelta(seconds=1)
    )
    assert verifier_numero_test(client, "+22670123456", code) is False
    assert NumeroTest.objects.get(id=numero.id).verifie_le is None


def test_dix_numeros_de_test_au_plus(passerelle):
    client = creer_client(raison_sociale="Découverte SA", forme=Client.Forme.SOCIETE)
    for rang in range(NumeroTest.MAXIMUM_PAR_CLIENT):
        declarer_numero_test(client, f"+2267000{rang:04d}")
    with pytest.raises(NumeroTestRefuse, match="10 numéros"):
        declarer_numero_test(client, "+22679999999")
