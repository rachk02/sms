"""Connexion, verrouillage et sessions, T-33."""

from datetime import timedelta

import pytest
import redis
from django.conf import settings
from django.db import connections
from django.test import Client as Navigateur
from django.utils import timezone

from apps.comptes import authentification
from apps.comptes.authentification import MAX_ECHECS, _cle
from apps.comptes.models import Client, Connexion, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.comptes.views import MESSAGE_ECHEC, MESSAGE_VERROUILLE
from apps.core.redis import client_redis

pytestmark = pytest.mark.django_db(databases=["default", "backoffice"])

MOT_DE_PASSE = "une-phrase-de-passe-solide"


@pytest.fixture(autouse=True)
def compteurs_vides():
    for courriel in ("awa@exemple.bf", "inconnu@exemple.bf", "agent@el-immeka.test"):
        client_redis().delete(_cle(courriel))
    yield
    for courriel in ("awa@exemple.bf", "inconnu@exemple.bf", "agent@el-immeka.test"):
        client_redis().delete(_cle(courriel))


@pytest.fixture
def utilisateur():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    return creer_utilisateur(
        courriel="awa@exemple.bf",
        mot_de_passe=MOT_DE_PASSE,
        nom="Awa",
        client=client,
        role=Utilisateur.Role.ADMIN,
        courriel_verifie_le=timezone.now(),
    )


def _connecter(client, courriel, mot_de_passe):
    return client.post("/connexion", {"courriel": courriel, "mot_de_passe": mot_de_passe})


def test_une_connexion_reussie_mene_a_l_accueil(client, utilisateur):
    reponse = _connecter(client, "Awa@Exemple.bf", MOT_DE_PASSE)
    assert reponse.status_code == 302 and reponse["Location"] == "/"
    assert "Exemple SA" in client.get("/").content.decode()


def test_cinq_echecs_verrouillent_pour_une_duree_annoncee(client, utilisateur):
    for _ in range(MAX_ECHECS - 1):
        assert MESSAGE_ECHEC in _connecter(client, "awa@exemple.bf", "faux").content.decode()
    assert MESSAGE_VERROUILLE in _connecter(client, "awa@exemple.bf", "faux").content.decode()
    assert "15 minutes" in MESSAGE_VERROUILLE
    # Même avec le bon mot de passe, le compte reste fermé.
    reponse = _connecter(client, "awa@exemple.bf", MOT_DE_PASSE)
    assert reponse.status_code == 200 and MESSAGE_VERROUILLE in reponse.content.decode()
    utilisateur.refresh_from_db()
    assert utilisateur.verrouille_jusqu_a > timezone.now() + timedelta(minutes=14)


def test_le_verrouillage_ne_revele_pas_si_l_adresse_existe(client, utilisateur):
    for courriel in ("awa@exemple.bf", "inconnu@exemple.bf"):
        textes = [
            _connecter(client, courriel, "faux").content.decode().split('role="alert">')[1][:120]
            for _ in range(MAX_ECHECS + 1)
        ]
        if courriel == "awa@exemple.bf":
            reference = textes
        else:
            assert textes == reference


def test_une_reussite_remet_le_compteur_a_zero(client, utilisateur):
    for _ in range(MAX_ECHECS - 1):
        _connecter(client, "awa@exemple.bf", "faux")
    assert _connecter(client, "awa@exemple.bf", MOT_DE_PASSE).status_code == 302
    utilisateur.refresh_from_db()
    assert utilisateur.echecs_consecutifs == 0 and utilisateur.verrouille_jusqu_a is None


def test_un_compte_dont_l_adresse_n_est_pas_verifiee_ne_se_connecte_pas(client, utilisateur):
    Utilisateur.objects.filter(pk=utilisateur.pk).update(courriel_verifie_le=None)
    assert _connecter(client, "awa@exemple.bf", MOT_DE_PASSE).status_code == 200


# Transactions réelles : la lecture passe par une autre connexion.
@pytest.mark.django_db(transaction=True, databases=["default", "backoffice"])
def test_chaque_tentative_est_journalisee(client, utilisateur):
    _connecter(client, "awa@exemple.bf", "faux")
    _connecter(client, "inconnu@exemple.bf", "faux")
    _connecter(client, "awa@exemple.bf", MOT_DE_PASSE)
    assert not Connexion.objects.exists()  # le rôle applicatif écrit sans lire
    lignes = list(
        Connexion.objects.using("backoffice")
        .order_by("horodatage")
        .values_list("courriel_saisi", "reussie", "motif")
    )
    assert lignes[-3:] == [
        ("awa@exemple.bf", False, "MOT_DE_PASSE"),
        ("inconnu@exemple.bf", False, "INCONNU"),
        ("awa@exemple.bf", True, ""),
    ]


def test_une_session_inactive_expire_et_la_reconnexion_ramene_a_la_page(client, utilisateur):
    assert settings.SESSION_COOKIE_AGE == 30 * 60 and settings.SESSION_SAVE_EVERY_REQUEST
    _connecter(client, "awa@exemple.bf", MOT_DE_PASSE)
    with connections["default"].cursor() as curseur:
        curseur.execute(
            "UPDATE django_session SET expire_date = now() - interval '1 minute'"
            " WHERE session_key = %s",
            [client.session.session_key],
        )
    reponse = client.get("/")
    assert reponse.status_code == 302 and reponse["Location"] == "/connexion?next=/"
    reponse = client.post(
        "/connexion", {"courriel": "awa@exemple.bf", "mot_de_passe": MOT_DE_PASSE, "next": "/"}
    )
    assert reponse["Location"] == "/"


def test_une_adresse_de_retour_externe_est_ignoree(client, utilisateur):
    reponse = client.post(
        "/connexion",
        {
            "courriel": "awa@exemple.bf",
            "mot_de_passe": MOT_DE_PASSE,
            "next": "https://pirate.test/",
        },
    )
    assert reponse["Location"] == "/"


@pytest.mark.django_db(transaction=True, databases=["default", "backoffice"])
def test_l_administration_du_back_office_est_verrouillee_aussi(client, en_tant_que_backoffice):
    creer_utilisateur(
        courriel="agent@el-immeka.test", mot_de_passe=MOT_DE_PASSE, nom="Agent", is_staff=True
    )
    for _ in range(MAX_ECHECS):
        client.post("/admin/login/", {"username": "agent@el-immeka.test", "password": "faux"})
    reponse = client.post(
        "/admin/login/", {"username": "agent@el-immeka.test", "password": MOT_DE_PASSE}
    )
    assert reponse.status_code == 200  # refusé : la page de connexion revient


def test_le_bandeau_du_bac_a_sable_montre_l_etat_reel_du_dossier(client, utilisateur):
    from apps.comptes.models import Dossier

    client.force_login(utilisateur)
    assert "Aucun dossier déposé" in client.get("/").content.decode()
    dossier = Dossier.objects.create(
        client=utilisateur.client,
        etat=Dossier.Etat.REJETE,
        motif_rejet=Dossier.MotifRejet.PIECE_EXPIREE,
        motif_detail="IFU de 2023",
    )
    page = client.get("/").content.decode()
    assert "bac à sable" in page and "rejeté" in page and "Pièce expirée" in page
    assert "IFU de 2023" in page
    Dossier.objects.filter(id=dossier.id).update(etat=Dossier.Etat.APPROUVE)
    Client.objects.filter(id=utilisateur.client_id).update(palier=Client.Palier.VERIFIE)
    assert "bac à sable" not in client.get("/").content.decode()


def test_redis_indisponible_la_connexion_reste_fermee(utilisateur, monkeypatch):
    """Décision A1 du 9 novembre 2026 : sans Redis, pas de verrouillage, donc
    pas de connexion. Aucun repli ici, contrairement aux quotas de l'API."""
    eteint = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    monkeypatch.setattr(authentification, "client_redis", lambda: eteint)
    navigateur = Navigateur(raise_request_exception=False)
    reponse = navigateur.post(
        "/connexion", {"courriel": "awa@exemple.bf", "mot_de_passe": MOT_DE_PASSE}
    )
    assert reponse.status_code == 500
    assert "_auth_user_id" not in navigateur.session
