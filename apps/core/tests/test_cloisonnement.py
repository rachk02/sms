import psycopg
import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.db import connection, transaction
from django.http import HttpResponse
from django.test import RequestFactory

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.cloisonnement import client_pose, poser_client
from apps.core.middleware import CloisonnementMiddleware


def _requete(utilisateur):
    requete = RequestFactory().get("/")
    requete.user = utilisateur
    return requete


def _middleware_qui_note_le_client(note):
    def vue(requete):
        note["client"] = client_pose()
        return HttpResponse()

    return CloisonnementMiddleware(vue)


def _client_et_utilisateur():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    utilisateur = creer_utilisateur(
        courriel="u@exemple.bf", mot_de_passe="x-1234-abcd", nom="U", client=client
    )
    return client, utilisateur


@pytest.mark.django_db
def test_le_middleware_pose_le_client_d_un_utilisateur_authentifie():
    client, utilisateur = _client_et_utilisateur()
    note = {}
    _middleware_qui_note_le_client(note)(_requete(utilisateur))
    assert note["client"] == str(client.id)


@pytest.mark.django_db
def test_le_middleware_laisse_le_reglage_vide_pour_un_anonyme():
    note = {}
    _middleware_qui_note_le_client(note)(_requete(AnonymousUser()))
    assert note["client"] == ""


@pytest.mark.django_db(transaction=True)
def test_le_reglage_disparait_a_la_fin_de_la_transaction_de_la_requete():
    client, utilisateur = _client_et_utilisateur()
    note = {}
    _middleware_qui_note_le_client(note)(_requete(utilisateur))
    assert note["client"] == str(client.id)
    assert client_pose() == ""


@pytest.mark.django_db(transaction=True)
def test_le_reglage_n_est_pas_visible_depuis_une_autre_connexion():
    client, _ = _client_et_utilisateur()
    base = settings.DATABASES["default"]
    autre = psycopg.connect(
        dbname=connection.settings_dict["NAME"],
        user=base["USER"],
        password=base["PASSWORD"],
        host=base["HOST"],
        port=base["PORT"],
    )
    try:
        with transaction.atomic():
            poser_client(client.id)
            assert client_pose() == str(client.id)
            valeur = autre.execute("SELECT current_setting('app.client', true)").fetchone()[0]
            assert not valeur
    finally:
        autre.close()


@pytest.mark.django_db(transaction=True)
def test_poser_un_client_hors_transaction_est_refuse():
    with pytest.raises(RuntimeError):
        poser_client(None)


@pytest.mark.django_db
def test_une_page_servie_par_la_pile_complete_traverse_le_middleware(client):
    assert client.get("/admin/login/").status_code == 200
