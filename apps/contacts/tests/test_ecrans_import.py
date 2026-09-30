"""Écrans de l'import, du dépôt au rapport, par le portail.

Le test lit la base par la connexion du worker : sous le rôle applicatif,
sans client posé, le cloisonnement lui masquerait tout."""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import connections
from django.utils import timezone

from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts import imports
from apps.contacts.models import Contact, Import

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

FICHIER = "Téléphone;Nom;Code client\n70123456;Awa;C1\n7012;Ali;C2\n71000001;Moussa;C3\n"


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


def _traiter_comme_le_worker(import_id):
    reglages = connections["default"].settings_dict
    avant = reglages["USER"], reglages["PASSWORD"]
    connections["default"].close()
    reglages["USER"] = connections["worker"].settings_dict["USER"]
    reglages["PASSWORD"] = connections["worker"].settings_dict["PASSWORD"]
    try:
        imports.traiter(import_id)
    finally:
        connections["default"].close()
        reglages["USER"], reglages["PASSWORD"] = avant


def test_du_depot_au_rapport_par_le_portail(client, membre):
    client.force_login(membre)
    fichier = SimpleUploadedFile("contacts.csv", FICHIER.encode(), "text/csv")
    reponse = client.post("/contacts/import", {"fichier": fichier})
    import_ = Import.objects.using("worker").get()
    assert reponse["Location"] == f"/contacts/import/{import_.pk}/mappage"

    page = client.get(reponse["Location"]).content.decode()
    assert "Awa" in page and 'value="numero" selected' in page and "checked" in page

    reponse = client.post(
        f"/contacts/import/{import_.pk}/mappage",
        {"entetes": "oui", "colonne_0": "numero", "colonne_1": "nom", "colonne_2": "personnalise"},
    )
    assert reponse["Location"] == f"/contacts/import/{import_.pk}"
    en_file = client.get(f"/contacts/import/{import_.pk}/fragments/progression").content.decode()
    assert "hx-trigger" in en_file  # la page se rafraîchit tant que ce n'est pas fini

    _traiter_comme_le_worker(import_.pk)

    fini = client.get(f"/contacts/import/{import_.pk}/fragments/progression").content.decode()
    assert "hx-trigger" not in fini and "numéro trop court" in fini
    assert Contact.objects.using("worker").get(numero_normalise="+22670123456").champs == {
        "code_client": "C1"
    }
    rapport = b"".join(client.get(f"/contacts/import/{import_.pk}/rejets.csv").streaming_content)
    assert b"7012;Ali;C2" in rapport


def test_un_fichier_refuse_est_explique(client, membre):
    client.force_login(membre)
    fichier = SimpleUploadedFile("piece.pdf", b"%PDF-1.7 binaire", "application/pdf")
    page = client.post("/contacts/import", {"fichier": fichier}).content.decode()
    assert "reconnu au contenu" in page


def test_un_mappage_sans_numero_est_refuse_avec_un_message(client, membre):
    client.force_login(membre)
    fichier = SimpleUploadedFile("contacts.csv", FICHIER.encode(), "text/csv")
    client.post("/contacts/import", {"fichier": fichier})
    import_ = Import.objects.using("worker").get()
    page = client.post(
        f"/contacts/import/{import_.pk}/mappage", {"entetes": "oui", "colonne_1": "nom"}
    ).content.decode()
    assert "colonne qui porte le numéro" in page
    assert Import.objects.using("worker").get(pk=import_.pk).etat == Import.Etat.DEPOSE


def test_l_import_d_un_autre_compte_est_introuvable(client, membre):
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    etranger = creer_utilisateur(
        courriel="x@autre.bf",
        mot_de_passe="x-1234-abcd",
        nom="X",
        client=autre,
        courriel_verifie_le=timezone.now(),
    )
    client.force_login(etranger)
    fichier = SimpleUploadedFile("contacts.csv", FICHIER.encode(), "text/csv")
    client.post("/contacts/import", {"fichier": fichier})
    import_etranger = Import.objects.using("worker").get()
    client.force_login(membre)
    assert client.get(f"/contacts/import/{import_etranger.pk}").status_code == 404
