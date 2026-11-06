"""Dossier de preuve d'un numéro au back-office, T-75."""

import html
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts import liste_noire
from apps.contacts.models import Contact, ListeNoire
from apps.contacts.services import creer_contact
from apps.journal.models import Message
from apps.journal.services import enregistrer_message
from apps.telecom.models import Connecteur, MessageEntrant, Operateur

pytestmark = pytest.mark.django_db(transaction=True)

NUMERO = "+22670123456"


@pytest.fixture
def agent(en_tant_que_backoffice):
    return creer_utilisateur(
        courriel="agent@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Agent",
        is_staff=True,
        is_superuser=True,
    )


def _message(client, texte, il_y_a, nature=Message.Nature.CLIENT, parti=True):
    message = enregistrer_message(
        client=client,
        numero_normalise=NUMERO,
        operateur=Operateur.ORANGE,
        expediteur_libelle="BOUTIQUE",
        texte=texte,
        cout_unitaire=0 if nature == Message.Nature.SYSTEME else 25,
        date_reception=timezone.now() - il_y_a,
        nature=nature,
    )
    if parti:
        Message.objects.filter(id=message.id).update(
            etat="SOUMIS", date_soumission=message.date_reception
        )
    return message


@pytest.fixture
def dossier(agent):
    boutique = creer_client(raison_sociale="Boutique SA", forme=Client.Forme.SOCIETE)
    banque = creer_client(raison_sociale="Banque SA", forme=Client.Forme.SOCIETE)
    creer_contact(client=boutique, numero="70 12 34 56", origine=Contact.Origine.IMPORT)
    _message(boutique, "Promotion de novembre", timedelta(days=3))
    _message(boutique, "Promotion de la semaine", timedelta(hours=5))
    _message(banque, "Votre relevé est disponible", timedelta(days=1))
    _message(banque, "Refusé avant le réseau", timedelta(hours=2), parti=False)
    liste_noire.bloquer(
        client=boutique,
        numero=NUMERO,
        origine=ListeNoire.Origine.STOP_SMS,
        message_origine="Stop svp",
    )
    _message(boutique, "Vous ne recevrez plus de SMS de BOUTIQUE.", timedelta(hours=4), "SYSTEME")
    connecteur = Connecteur.objects.create(
        nom="simule-orange", operateur=Operateur.ORANGE, type=Connecteur.Type.SIMULE
    )
    MessageEntrant.objects.create(
        client=boutique,
        connecteur=connecteur,
        reference="mo-1",
        numero_expediteur="22670123456",  # tel que la passerelle le rend
        numero_destination="36000",
        texte="Stop svp",
        mot_cle_reconnu="STOP",
        recu_le=timezone.now() - timedelta(hours=4),
        traite_le=timezone.now(),
    )
    return boutique, banque


def _page(reponse):
    assert reponse.status_code == 200
    return html.unescape(reponse.content.decode())


def test_en_trois_clics_l_agent_retrouve_qui_quand_comment_et_combien(client, agent, dossier):
    boutique, banque = dossier
    client.force_login(agent)

    accueil = _page(client.get("/admin/"))
    assert "Dossiers de preuve" in accueil  # premier clic

    recherche = _page(client.get("/admin/backoffice/dossierpreuve/", {"numero": "70 12 34 56"}))
    assert f"{NUMERO} : 2 clients" in recherche  # deuxième clic
    assert "Import de fichier" in recherche and "Non déclaré" in recherche
    assert "Pas dans ses contacts" in recherche  # la banque a écrit à un numéro libre
    assert "Stop svp" in recherche and "Boutique SA" in recherche

    detail = _page(  # troisième clic
        client.get(
            "/admin/backoffice/dossierpreuve/client/", {"numero": NUMERO, "client": boutique.id}
        )
    )
    assert "Message de désinscription d'origine : « Stop svp »" in detail
    assert "2 messages du client reçus" in detail
    assert "Promotion de novembre" in detail and "Promotion de la semaine" in detail
    assert "(confirmation de désinscription)" in detail
    assert "Votre relevé" not in detail  # un autre client


def test_le_comptage_se_fait_par_client_sur_douze_mois(agent, dossier):
    from apps.backoffice import selectors

    boutique, banque = dossier
    lu = selectors.dossier(NUMERO)
    comptes = {ligne.client.raison_sociale: ligne.messages for ligne in lu.clients}
    assert comptes == {"Boutique SA": 2, "Banque SA": 1}  # sans la confirmation
    assert timezone.now() - lu.depuis >= timedelta(days=364)
    assert [e.texte for e in lu.desinscriptions] == ["Stop svp"]


def test_un_numero_invalide_est_signale(client, agent):
    client.force_login(agent)
    page = _page(client.get("/admin/backoffice/dossierpreuve/", {"numero": "12"}))
    assert "Numéro invalide" in page


def test_un_numero_inconnu_le_dit(client, agent):
    client.force_login(agent)
    page = _page(client.get("/admin/backoffice/dossierpreuve/", {"numero": "+22676000000"}))
    assert "Aucun client ne connaît ce numéro" in page
