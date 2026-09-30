"""Liste noire, par client et de la plateforme, T-51."""

import pytest
from django.conf import settings
from django.db import ProgrammingError, transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts import liste_noire
from apps.contacts.models import Contact, ListeNoire
from apps.contacts.services import creer_contact
from apps.core.cloisonnement import poser_client
from apps.core.etats import Etat
from apps.core.models import Audit
from apps.journal.services import enregistrer_message, soumettre
from apps.telecom import correlation
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import passerelle_pour

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def client(en_tant_que_worker):
    return creer_client(
        raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )


@pytest.fixture
def passerelle(client):
    connecteur = Connecteur.objects.create(
        nom=f"c-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SIMULE,
    )
    return passerelle_pour(connecteur)


def _message(client, numero):
    return enregistrer_message(
        client=client,
        numero_normalise=numero,
        operateur="ORANGE",
        expediteur_libelle=settings.SMS_NUMERO_COURT,
        texte="Promotion",
        cout_unitaire=10,
    )


def _soumettre(message, passerelle):
    soumis = soumettre(message, passerelle)
    if soumis.reference_smsc:
        correlation._redis().delete(
            correlation.cle_redis(passerelle.connecteur.id, soumis.reference_smsc)
        )
    return soumis


def test_un_numero_bloque_pendant_la_diffusion_ne_recoit_pas_la_suite(client, passerelle):
    """Le second contrôle, juste avant la soumission : la campagne est déjà
    constituée quand le STOP arrive."""
    premier, second = _message(client, "+22670123456"), _message(client, "+22670123456")
    assert _soumettre(premier, passerelle).etat == Etat.SOUMIS
    liste_noire.bloquer(
        client=client, numero="70123456", origine="STOP_SMS", message_origine="STOP merci"
    )
    refuse = _soumettre(second, passerelle)
    assert (refuse.etat, refuse.motif_echec) == (Etat.REJETE, liste_noire.MOTIF_LISTE_NOIRE)
    assert len(passerelle.soumissions) == 1


def test_la_liste_noire_de_la_plateforme_vaut_pour_tous_les_clients(client, passerelle):
    agent = creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x", nom="Agent")
    liste_noire.bloquer_partout(
        numero="+22671000000", origine="RECLAMATION", motif="Plainte ARCEP 2026-114", agent=agent
    )
    assert _soumettre(_message(client, "+22671000000"), passerelle).etat == Etat.REJETE
    entree = Audit.objects.get(action="contacts.liste_noire.plateforme")
    assert entree.motif == "Plainte ARCEP 2026-114"


def test_le_nombre_de_destinataires_annonce_tient_compte_de_la_liste_noire(client):
    for numero in ("70000001", "70000002", "70000003", "70000004"):
        creer_contact(client=client, numero=numero, origine="SAISIE")
    liste_noire.bloquer(client=client, numero="70000002", origine="MANUEL")
    agent = creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x", nom="Agent")
    liste_noire.bloquer_partout(numero="70000003", origine="RECLAMATION", motif="x", agent=agent)
    contacts = Contact.objects.filter(client=client)
    assert liste_noire.destinataires(client.id, contacts).count() == 2


def test_une_meme_desinscription_recue_deux_fois_reste_une_ligne(client):
    liste_noire.bloquer(
        client=client, numero="70123456", origine="STOP_SMS", message_origine="STOP"
    )
    liste_noire.bloquer(
        client=client, numero="+22670123456", origine="STOP_SMS", message_origine="stop"
    )
    assert ListeNoire.objects.filter(numero_normalise="+22670123456").count() == 1
    assert ListeNoire.objects.get().message_origine == "STOP"  # la preuve d'origine est gardée


def test_un_client_lit_la_liste_de_la_plateforme_sans_pouvoir_y_ecrire():
    """Sous le rôle applicatif."""
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    with transaction.atomic():
        poser_client(autre.id)
        liste_noire.bloquer(client=autre, numero="70999999", origine="MANUEL")
    with transaction.atomic():
        poser_client(client.id)
        liste_noire.bloquer(client=client, numero="70111111", origine="MANUEL")
        assert list(ListeNoire.objects.values_list("numero_normalise", flat=True)) == [
            "+22670111111"
        ]
    with pytest.raises(ProgrammingError, match="row-level security"), transaction.atomic():
        poser_client(client.id)
        ListeNoire.objects.create(client=None, numero_normalise="+22670222222", origine="MANUEL")
