"""Écran du répertoire : recherche, page suivante, demande d'export (T-53)."""

import re

import pytest
from django.utils import timezone

from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts import recherche
from apps.contacts.models import Contact, Export

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def membre():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    return creer_utilisateur(
        courriel="u@exemple.bf",
        mot_de_passe="x-1234-abcd",
        nom="U",
        client=client,
        role=Utilisateur.Role.UTILISATEUR,
        courriel_verifie_le=timezone.now(),
    )


def _contacts(client_id, *numeros):
    Contact.objects.using("worker").bulk_create(
        Contact(
            client_id=client_id,
            numero_normalise=numero,
            numero_origine=numero,
            nom=f"Nom {numero[-2:]}",
            origine="SAISIE",
        )
        for numero in numeros
    )


def test_la_recherche_ne_montre_que_les_contacts_du_compte(client, membre):
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    _contacts(membre.client_id, "+22670000001", "+22671000002")
    _contacts(autre.id, "+22670000003")
    client.force_login(membre)
    page = client.get("/contacts").content.decode()
    assert "+22670000001" in page and "+22671000002" in page and "+22670000003" not in page
    fragment = client.get("/contacts/fragments/resultats", {"numero": "70"}).content.decode()
    assert 'id="resultats"' in fragment and "+22670000001" in fragment
    assert "+22671000002" not in fragment


def test_la_page_suivante_s_ajoute_sous_les_lignes(client, membre, monkeypatch):
    monkeypatch.setattr(recherche, "TAILLE_PAGE", 2)
    _contacts(membre.client_id, "+22670000001", "+22670000002", "+22670000003")
    client.force_login(membre)
    page = client.get("/contacts", {"numero": "70"}).content.decode()
    assert "Afficher la suite" in page
    suivante = re.search(r"apres=([\w-]+)", page).group(1)
    suite = client.get("/contacts/fragments/resultats", {"numero": "70", "apres": suivante})
    lignes = suite.content.decode()
    assert 'id="resultats"' not in lignes and lignes.count("<tr") == 1
    assert "Afficher la suite" not in lignes


def test_un_curseur_trafique_est_explique(client, membre):
    client.force_login(membre)
    page = client.get("/contacts", {"apres": "xyz"}).content.decode()
    assert "Curseur de pagination invalide" in page


def test_l_export_se_demande_et_s_annonce(client, membre):
    _contacts(membre.client_id, "+22670000001")
    client.force_login(membre)
    reponse = client.post("/contacts/export", {"operateur": "ORANGE"}, follow=True)
    assert "le lien de téléchargement vous parviendra à u@exemple.bf" in reponse.content.decode()
    export = Export.objects.using("worker").get()
    assert export.filtres["operateur"] == "ORANGE" and export.courriel == "u@exemple.bf"
