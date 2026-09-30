"""Contrôle de l'expéditeur à la soumission et politiques de campagne (T-60)."""

import io
from collections import Counter

import pytest
from django.conf import settings
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.etats import Etat
from apps.core.models import EvenementSortant
from apps.expediteurs import services
from apps.expediteurs.controle import MOTIF_EXPEDITEUR, Action, Politique, choisir
from apps.expediteurs.models import Expediteur
from apps.journal.services import enregistrer_message, soumettre
from apps.telecom import correlation
from apps.telecom.models import Connecteur
from apps.telecom.passerelles import passerelle_pour

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def agent(en_tant_que_worker):
    return creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x", nom="Agent")


@pytest.fixture
def client(en_tant_que_worker):
    return creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )


@pytest.fixture
def fasoriz(client, agent):
    """Approuvé chez Orange seulement."""
    expediteur = services.declarer(
        client=client,
        libelle="FASORIZ",
        mandat=io.BytesIO(b"%PDF-1.7 mandat"),
        nom_mandat="mandat.pdf",
        auteur=None,
    )
    services.deposer_dossier(expediteur.id, "ORANGE", agent=agent, reference="OBF-1")
    services.decider(
        expediteur.id,
        "ORANGE",
        agent=agent,
        approuve=True,
        decide_le=timezone.now(),
        reference="OBF-1",
    )
    return expediteur


def _soumettre(client, operateur, expediteur):
    connecteur = Connecteur.objects.create(
        nom=f"c-{operateur}-{timezone.now().timestamp()}",
        operateur=operateur,
        type=Connecteur.Type.SIMULE,
    )
    passerelle = passerelle_pour(connecteur)
    message = enregistrer_message(
        client=client,
        numero_normalise="+22670123456",
        operateur=operateur,
        expediteur_libelle=expediteur,
        texte="Promotion",
        cout_unitaire=10,
    )
    soumis = soumettre(message, passerelle)
    if soumis.reference_smsc:
        correlation._redis().delete(correlation.cle_redis(connecteur.id, soumis.reference_smsc))
    return soumis


def test_un_identifiant_ne_part_que_sur_le_reseau_qui_l_a_approuve(client, fasoriz):
    assert _soumettre(client, "ORANGE", "FASORIZ").etat == Etat.SOUMIS
    refuse = _soumettre(client, "MOOV", "FASORIZ")
    assert (refuse.etat, refuse.motif_echec) == (Etat.REJETE, MOTIF_EXPEDITEUR)
    assert _soumettre(client, "MOOV", settings.SMS_NUMERO_COURT).etat == Etat.SOUMIS


def test_un_identifiant_approuve_d_un_autre_compte_est_refuse(client, fasoriz):
    usurpateur = creer_client(
        raison_sociale="Autre SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    assert _soumettre(usurpateur, "ORANGE", "FASORIZ").motif_echec == MOTIF_EXPEDITEUR


def test_un_identifiant_suspendu_ne_part_plus(client, fasoriz):
    Expediteur.objects.filter(id=fasoriz.id).update(etat=Expediteur.Etat.SUSPENDU)
    assert _soumettre(client, "ORANGE", "FASORIZ").etat == Etat.REJETE


def test_l_approbation_publie_un_evenement_pour_les_campagnes_en_attente(fasoriz):
    evenement = EvenementSortant.objects.get(nom="expediteurs.reseau.approuve")
    assert evenement.donnees == {"libelle": "FASORIZ", "operateur": "ORANGE"}


@pytest.mark.parametrize(
    "politique, attendu",
    [
        (
            Politique.REFUSER,
            {(Action.ENVOYER, "FASORIZ"): 40, (Action.REFUSER, ""): 60},
        ),
        (
            Politique.REPLI,
            {(Action.ENVOYER, "FASORIZ"): 40, (Action.REPLI, "36000"): 60},
        ),
        (
            Politique.ATTENDRE,
            {(Action.ENVOYER, "FASORIZ"): 40, (Action.ATTENDRE, "FASORIZ"): 60},
        ),
    ],
)
def test_un_lot_melange_suit_la_politique_choisie(client, fasoriz, settings, politique, attendu):
    settings.SMS_NUMERO_COURT = "36000"
    lot = ["ORANGE"] * 40 + ["MOOV"] * 35 + ["TELECEL"] * 25
    decisions = Counter(
        (choix.action, choix.expediteur)
        for choix in (choisir(client.id, "FASORIZ", operateur, politique) for operateur in lot)
    )
    assert decisions == attendu
