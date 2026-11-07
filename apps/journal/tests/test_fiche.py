"""Fiche d'un message (T-94) : pourquoi ce message n'est pas arrivé."""

import json
import uuid
from datetime import timedelta
from urllib.parse import urlencode

import pytest
from django.db import connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.etats import Etat
from apps.journal import selectors
from apps.journal.models import Evenement, Message
from apps.journal.services import changer_etat, enregistrer_message
from apps.telecom.models import Connecteur
from apps.telecom.supervision import publier_etat_liaisons

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def compte(en_tant_que_worker):
    return creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)


@pytest.fixture
def membre(compte):
    return creer_utilisateur(
        courriel="awa@fasoriz.bf",
        mot_de_passe="x-1234-abcd",
        nom="Awa",
        client=compte,
        courriel_verifie_le=timezone.now(),
    )


@pytest.fixture
def liaison(compte):
    """Une liaison réelle, publiée par l'ordonnanceur : le rôle web ne lit pas
    la table des connecteurs."""
    connecteur = Connecteur.objects.create(
        nom=f"orange-smpp-{uuid.uuid4().hex[:4]}",
        operateur="ORANGE",
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        debit_max=100,
    )
    publier_etat_liaisons()
    return connecteur


def _message(client):
    return enregistrer_message(
        client=client,
        numero_normalise="+22670123456",
        operateur="ORANGE",
        expediteur_libelle="FASORIZ",
        texte="Votre commande est prête",
        cout_unitaire=25,
        date_reception=timezone.now() - timedelta(minutes=30),
    )


def _passer(message, vers, **champs):
    with transaction.atomic():
        courant = Message.objects.select_for_update().get(
            id=message.id, date_reception=message.date_reception
        )
        changer_etat(courant, vers, **champs)


def _non_livre(client, liaison):
    message = _message(client)
    soumis_le = timezone.now() - timedelta(minutes=29)
    _passer(
        message,
        Etat.SOUMIS,
        source=Evenement.Source.WORKER,
        horodatage=soumis_le,
        details={"reference_smsc": "A1B2C3"},
        reference_smsc="A1B2C3",
        connecteur_id=liaison.id,
        date_soumission=soumis_le,
    )
    _passer(
        message,
        Etat.NON_LIVRE,
        source=Evenement.Source.ACCUSE,
        horodatage=soumis_le + timedelta(minutes=20),
        details={"libelle_operateur": "UNDELIV"},
        date_accuse=soumis_le + timedelta(minutes=20),
    )
    return message


def test_la_fiche_dit_pourquoi_le_message_n_est_pas_arrive(compte, liaison):
    message = _non_livre(compte, liaison)
    fiche = selectors.fiche(compte.id, message.id, message.date_reception)
    assert [e.etat for e in fiche.etapes] == [Etat.RECU, Etat.SOUMIS, Etat.NON_LIVRE]
    assert fiche.liaison == liaison.nom
    assert "ORANGE a renoncé à le livrer" in fiche.diagnostic
    assert "UNDELIV" in fiche.diagnostic

    contexte = fiche.contexte_ticket()
    assert contexte["message_id"] == str(message.id)
    assert (contexte["etat"], contexte["liaison"], contexte["reference_smsc"]) == (
        Etat.NON_LIVRE,
        liaison.nom,
        "A1B2C3",
    )
    assert [e["etat"] for e in contexte["historique"]] == ["RECU", "SOUMIS", "NON_LIVRE"]
    json.dumps(contexte)  # transportable tel quel dans un ticket


def test_un_refus_avant_le_reseau_nomme_sa_cause(compte):
    message = _message(compte)
    _passer(
        message,
        Etat.REJETE,
        source=Evenement.Source.WORKER,
        details={"motif": "LISTE_NOIRE"},
        motif_echec="LISTE_NOIRE",
    )
    fiche = selectors.fiche(compte.id, message.id, message.date_reception)
    assert fiche.liaison == ""
    assert "liste noire" in fiche.diagnostic and "pas parti" in fiche.diagnostic


def test_l_ecran_borne_la_lecture_et_cloisonne(client, membre, liaison):
    message = _non_livre(membre.client, liaison)
    autre = creer_client(raison_sociale="Banque SA", forme=Client.Forme.SOCIETE)
    etranger = _message(autre)
    client.force_login(membre)
    adresse = f"/journal/messages/{message.id}"

    recu = urlencode({"recu": message.date_reception.isoformat()})
    with CaptureQueriesContext(connections["default"]) as requetes:
        reponse = client.get(f"{adresse}?{recu}")
    page = reponse.content.decode()
    assert reponse.status_code == 200
    assert liaison.nom in page and "UNDELIV" in page and "Historique" in page
    lectures = [q["sql"] for q in requetes.captured_queries if "journal_message" in q["sql"]]
    # Bornée sur la clé de partition : la date figure dans la condition, pas
    # seulement parmi les colonnes lues.
    assert lectures and all("date_reception" in sql.split("WHERE", 1)[1] for sql in lectures)

    # Sans la date de réception, pas de lecture ; le message d'un autre compte est introuvable.
    assert client.get(adresse).status_code == 404
    recu = urlencode({"recu": etranger.date_reception.isoformat()})
    assert client.get(f"/journal/messages/{etranger.id}?{recu}").status_code == 404
