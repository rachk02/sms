"""Écran des identifiants côté client (T-58, T-59).

Le test lit la base par la connexion du worker : sous le rôle applicatif, sans
client posé, le cloisonnement lui masquerait tout."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.utils import timezone

from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.expediteurs.models import Expediteur, ExpediteurReseau

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


def _membre(compte, role, courriel):
    return creer_utilisateur(
        courriel=courriel,
        mot_de_passe="x-1234-abcd",
        nom="U",
        client=compte,
        role=role,
        courriel_verifie_le=timezone.now(),
    )


@pytest.fixture
def compte():
    return creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)


def _mandat(contenu=b"%PDF-1.7 mandat"):
    return SimpleUploadedFile("mandat.pdf", contenu, "application/pdf")


def test_l_administrateur_declare_et_voit_trois_etats(client, compte):
    client.force_login(_membre(compte, Utilisateur.Role.ADMIN, "admin@fasoriz.bf"))
    page = client.post(
        "/expediteurs/declarer", {"libelle": "FASORIZ", "mandat": _mandat()}, follow=True
    ).content.decode()
    assert "FASORIZ déclaré" in page
    expediteur = Expediteur.objects.using("worker").get()
    ExpediteurReseau.objects.using("worker").filter(
        expediteur=expediteur, operateur="ORANGE"
    ).update(etat="APPROUVE", decide_le=timezone.now())
    ExpediteurReseau.objects.using("worker").filter(expediteur=expediteur, operateur="MOOV").update(
        etat="REFUSE", motif_refus="Marque non justifiée"
    )
    page = client.get("/expediteurs").content.decode()
    assert "Approuvé" in page and "Refusé" in page and "Non soumis" in page
    assert "Marque non justifiée" in page


def test_un_refus_de_declaration_est_explique(client, compte):
    client.force_login(_membre(compte, Utilisateur.Role.ADMIN, "admin@fasoriz.bf"))
    page = client.post(
        "/expediteurs/declarer", {"libelle": "FASORIZPROMO", "mandat": _mandat()}, follow=True
    ).content.decode()
    assert "11 caractères au plus" in page
    assert not Expediteur.objects.using("worker").exists()


def test_l_utilisateur_consulte_sans_formulaire_de_declaration(client, compte):
    client.force_login(_membre(compte, Utilisateur.Role.UTILISATEUR, "u@fasoriz.bf"))
    page = client.get("/expediteurs").content.decode()
    assert "Identifiants d'expéditeur" in page and "Déclarer un identifiant" not in page
