"""Écrans des listes, de la liste noire et des champs personnalisés.

Le test lit la base par la connexion du worker : sous le rôle applicatif, sans
client posé, le cloisonnement lui masquerait tout."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts.models import (
    ChampPersonnalise,
    Contact,
    Import,
    Liste,
    ListeNoire,
    Segment,
)

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


def _membre(client, role, courriel):
    return creer_utilisateur(
        courriel=courriel,
        mot_de_passe="x-1234-abcd",
        nom="U",
        client=client,
        role=role,
        courriel_verifie_le=timezone.now(),
    )


@pytest.fixture
def compte():
    return creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)


@pytest.fixture
def admin(compte):
    return _membre(compte, Utilisateur.Role.ADMIN, "admin@exemple.bf")


def test_une_liste_se_cree_puis_se_supprime_sans_toucher_aux_contacts(client, admin):
    client.force_login(admin)
    page = client.post("/contacts/listes", {"nom": "Fidèles"}, follow=True).content.decode()
    assert "Liste « Fidèles » créée" in page
    doublon = client.post("/contacts/listes", {"nom": "Fidèles"}, follow=True).content.decode()
    assert "existe déjà" in doublon
    liste = Liste.objects.using("worker").get()
    Contact.objects.using("worker").create(
        client_id=admin.client_id, numero_normalise="+22670000001", origine="SAISIE"
    )
    page = client.post(f"/contacts/listes/{liste.id}/supprimer", follow=True).content.decode()
    assert "restent dans le répertoire" in page
    assert not Liste.objects.using("worker").exists()
    assert Contact.objects.using("worker").count() == 1


def test_la_liste_d_un_autre_compte_ne_se_supprime_pas(client, admin):
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    sienne = Liste.objects.using("worker").create(client=autre, nom="À lui")
    client.force_login(admin)
    assert client.post(f"/contacts/listes/{sienne.id}/supprimer").status_code == 404
    assert Liste.objects.using("worker").filter(id=sienne.id).exists()


def test_un_numero_se_bloque_et_se_retrouve(client, admin):
    client.force_login(admin)
    page = client.post(
        "/contacts/liste-noire", {"numero": "70 12 34 56", "motif": "Demande orale"}, follow=True
    ).content.decode()
    assert "+22670123456 ne recevra plus vos messages" in page and "Demande orale" in page
    entree = ListeNoire.objects.using("worker").get()
    assert (entree.client_id, entree.origine) == (admin.client_id, "MANUEL")
    trouve = client.get("/contacts/liste-noire", {"numero": "+226 70123456"}).content.decode()
    assert "+22670123456" in trouve
    refus = client.post("/contacts/liste-noire", {"numero": "123"}, follow=True).content.decode()
    assert "Numéro invalide : numéro trop court" in refus


def test_un_champ_se_declare_et_sa_suppression_annonce_les_segments_desactives(client, admin):
    client.force_login(admin)
    vide = client.post(
        "/contacts/champs",
        {"libelle": "Secteur", "valeur_repli": " ", "type": "TEXTE"},
        follow=True,
    ).content.decode()
    assert "valeur de repli est obligatoire" in vide
    client.post(
        "/contacts/champs", {"libelle": "Secteur", "valeur_repli": "votre secteur", "type": "TEXTE"}
    )
    champ = ChampPersonnalise.objects.using("worker").get()
    Segment.objects.using("worker").create(
        client_id=admin.client_id,
        nom="Riziers",
        criteres=[{"champ": "secteur", "operation": "egal", "valeur": "riz"}],
    )
    page = client.post(f"/contacts/champs/{champ.id}/supprimer", follow=True).content.decode()
    assert "Segments désactivés, à revoir : Riziers" in page
    assert not Segment.objects.using("worker").get().actif


def _deposer(client):
    fichier = SimpleUploadedFile("salon.csv", b"numero\n70123456\n", "text/csv")
    return client.post("/contacts/import", {"fichier": fichier})["Location"]


def test_l_import_remplit_la_liste_choisie_au_mappage(client, admin):
    liste = Liste.objects.using("worker").create(client_id=admin.client_id, nom="Salon")
    client.force_login(admin)
    mappage = _deposer(client)
    assert "Salon" in client.get(mappage).content.decode()
    client.post(mappage, {"entetes": "oui", "colonne_0": "numero", "liste": str(liste.id)})
    assert Import.objects.using("worker").get().mappage["liste_id"] == str(liste.id)


def test_une_liste_inconnue_au_mappage_est_expliquee(client, admin):
    client.force_login(admin)
    mappage = _deposer(client)
    page = client.post(mappage, {"entetes": "oui", "colonne_0": "numero", "liste": "pas-un-id"})
    assert "Liste introuvable pour ce compte" in page.content.decode()
    assert Import.objects.using("worker").get().etat == Import.Etat.DEPOSE
