"""Stockage et chiffrement des pièces, T-38. Contre le vrai MinIO du docker-compose."""

import time
import urllib.error
import urllib.request
from datetime import timedelta

import pytest
from django.conf import settings

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.cloisonnement import poser_client
from apps.fichiers import services
from apps.fichiers.models import Fichier

pytestmark = pytest.mark.django_db

PDF = b"%PDF-1.7\n" + b"0" * 2048 + b"\n%%EOF"


@pytest.fixture
def client():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    poser_client(client.id)
    return client


def _deposer(client, contenu=PDF, nom="rccm de l'entreprise.pdf"):
    return services.deposer(
        client=client, famille=Fichier.Famille.PIECE, contenu=contenu, nom_origine=nom
    )


def _objet(fichier):
    return services._stockage().stat_object(settings.MINIO_BUCKET, fichier.chemin)


def test_une_piece_est_deposee_chiffree_sous_un_chemin_sans_son_nom(client):
    fichier = _deposer(client)
    assert fichier.chemin.startswith(f"clients/{client.id}/piece/")
    assert fichier.chemin.endswith(".pdf") and "rccm" not in fichier.chemin
    assert (fichier.nom_origine, fichier.type_mime, fichier.chiffre) == (
        "rccm de l'entreprise.pdf",
        "application/pdf",
        True,
    )
    objet = _objet(fichier)
    assert objet.size == len(PDF)
    entetes = {cle.lower(): valeur for cle, valeur in objet.metadata.items()}
    assert entetes.get("x-amz-server-side-encryption") == "AES256"


def test_un_fichier_deguise_est_refuse_sur_son_contenu_et_non_depose(client):
    executable = b"MZ\x90\x00" + b"\x00" * 512
    with pytest.raises(services.FichierRefuse, match="reconnu au contenu"):
        _deposer(client, executable, "piece.pdf")
    assert not Fichier.objects.exists()


def test_un_fichier_trop_lourd_ou_vide_est_refuse(client):
    with pytest.raises(services.FichierRefuse, match="trop lourd"):
        _deposer(client, b"%PDF-" + b"0" * (5 * 1024 * 1024))
    with pytest.raises(services.FichierRefuse, match="vide"):
        _deposer(client, b"")


def test_le_lien_signe_donne_le_fichier_puis_expire(client):
    fichier = _deposer(client)
    with urllib.request.urlopen(services.lien(fichier), timeout=10) as reponse:
        assert reponse.read() == PDF

    lien_court = services.lien(fichier, duree=timedelta(seconds=1))
    time.sleep(2)
    with pytest.raises(urllib.error.HTTPError) as refus:
        urllib.request.urlopen(lien_court, timeout=10)
    assert refus.value.code == 403


def test_le_lien_par_defaut_vaut_quinze_minutes(client):
    fichier = _deposer(client)
    assert "X-Amz-Expires=900" in services.lien(fichier)


def test_un_autre_client_ne_voit_pas_la_piece(client):
    _deposer(client)
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    poser_client(autre.id)
    assert not Fichier.objects.exists()
