import pytest
from django.core.management import call_command
from django.db import IntegrityError

from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import PLAFONDS_PAR_PALIER, creer_client, creer_utilisateur

pytestmark = pytest.mark.django_db


def test_createsuperuser_fonctionne_avec_un_courriel_et_sans_nom_d_utilisateur(monkeypatch):
    monkeypatch.setenv("DJANGO_SUPERUSER_PASSWORD", "une-phrase-de-passe-solide")
    call_command("createsuperuser", interactive=False, courriel="Agent@EL-IMMEKA.test", nom="Agent")

    agent = Utilisateur.objects.get(courriel="agent@el-immeka.test")
    assert agent.is_superuser and agent.is_staff
    assert agent.client is None
    assert not hasattr(agent, "username")


def test_deux_utilisateurs_ne_partagent_pas_un_courriel():
    creer_utilisateur(courriel="a@exemple.bf", mot_de_passe="x-1234-abcd", nom="A")
    with pytest.raises(IntegrityError):
        creer_utilisateur(courriel="A@Exemple.BF", mot_de_passe="x-1234-abcd", nom="B")


def test_le_condensat_du_mot_de_passe_est_argon2id():
    utilisateur = creer_utilisateur(courriel="b@exemple.bf", mot_de_passe="x-1234-abcd", nom="B")
    assert utilisateur.password.startswith("argon2$argon2id$")
    assert utilisateur.check_password("x-1234-abcd")


def test_la_connexion_retrouve_le_courriel_sans_tenir_compte_de_la_casse():
    creer_utilisateur(courriel="c@exemple.bf", mot_de_passe="x-1234-abcd", nom="C")
    assert Utilisateur.objects.get_by_natural_key("C@EXEMPLE.bf").courriel == "c@exemple.bf"


def test_un_client_recoit_les_plafonds_de_son_palier():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    assert client.palier == Client.Palier.DECOUVERTE
    assert client.plafond_horaire == PLAFONDS_PAR_PALIER[Client.Palier.DECOUVERTE]["horaire"]
    assert client.pays == "BF"


def test_deux_clients_ne_partagent_pas_un_rccm_mais_peuvent_n_en_avoir_aucun():
    creer_client(raison_sociale="A", forme=Client.Forme.ASSOCIATION)
    creer_client(raison_sociale="B", forme=Client.Forme.ASSOCIATION)
    creer_client(raison_sociale="C", forme=Client.Forme.SOCIETE, rccm="BF-OUA-2026-B-001")
    with pytest.raises(IntegrityError):
        creer_client(raison_sociale="D", forme=Client.Forme.SOCIETE, rccm="BF-OUA-2026-B-001")


def test_un_utilisateur_de_compte_est_rattache_a_son_client():
    client = creer_client(raison_sociale="E", forme=Client.Forme.PUBLIC)
    utilisateur = creer_utilisateur(
        courriel="d@exemple.bf",
        mot_de_passe="x-1234-abcd",
        nom="D",
        client=client,
        role=Utilisateur.Role.PROPRIETAIRE,
    )
    assert list(client.utilisateurs.all()) == [utilisateur]
