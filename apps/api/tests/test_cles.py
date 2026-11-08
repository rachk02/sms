"""Clés d'API (T-106).

Le secret complet n'est stocké nulle part, ce qu'un test vérifie en fouillant
la base. Une clé révoquée est refusée immédiatement, sans attendre l'expiration
d'un cache. La vérification d'une clé prend moins de dix millisecondes malgré
Argon2id, grâce au préfixe qui évite de tester toutes les clés.
"""

import re
import statistics
import time

import pytest
from django.db import connections, transaction
from django.test import override_settings
from django.utils import timezone

from apps.comptes.models import CleApi, Client
from apps.comptes.services import creer_cle, creer_client, creer_utilisateur, revoquer_cle
from apps.comptes.services import verifier_cle as verifier
from apps.core.cloisonnement import poser_client
from apps.core.models import Audit
from apps.core.redis import client_redis

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

ESSAI = "/v1/essai"


@pytest.fixture
def comptes(espaces):
    """Deux comptes, une clé chacun, créés sous le rôle worker ; le test
    repasse ensuite sous le rôle applicatif, celui du service web."""
    espaces("worker")
    faso = creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)
    autre = creer_client(raison_sociale="Autre SARL", forme=Client.Forme.SOCIETE)
    admin = creer_utilisateur(
        courriel="awa@fasoriz.bf",
        mot_de_passe="x-1234-abcd",
        nom="Awa",
        client=faso,
        role="ADMIN",
        courriel_verifie_le=timezone.now(),
    )
    cle, jeton = creer_cle(client=faso, nom="Site de commande", auteur=admin)
    _, jeton_autre = creer_cle(client=autre, nom="ERP", auteur=None)
    espaces("client")
    return {"faso": faso, "admin": admin, "cle": cle, "jeton": jeton, "autre": jeton_autre}


def _appel(client, jeton=None):
    entetes = {"HTTP_AUTHORIZATION": f"Bearer {jeton}"} if jeton else {}
    return client.get(ESSAI, **entetes)


@override_settings(ROOT_URLCONF="apps.api.tests.urls_essai")
def test_la_cle_pose_son_client_et_ne_voit_que_ses_cles(client, comptes):
    reponse = _appel(client, comptes["jeton"])
    assert reponse.status_code == 200
    assert reponse["Api-Version"] == "1"
    corps = reponse.json()
    assert corps["client"] == str(comptes["faso"].id)
    assert corps["cles_visibles"] == [comptes["cle"].prefixe]
    cle = CleApi.objects.using("worker").get(id=comptes["cle"].id)
    assert cle.derniere_utilisation_le is not None and cle.derniere_version == "1"


@override_settings(ROOT_URLCONF="apps.api.tests.urls_essai")
def test_une_cle_absente_fausse_ou_mal_formee_est_refusee(client, comptes):
    jeton = comptes["jeton"]
    _, environnement, prefixe, secret = jeton.split("_", 3)
    for essai in (
        None,
        "sk_live_inconnu_" + secret,
        f"sk_live_{prefixe}_{secret[:-2]}xx",
        f"sk_test_{prefixe}_{secret}",
        "n-importe-quoi",
        f"Basic {jeton}",
    ):
        reponse = _appel(client, essai)
        assert reponse.status_code == 401, essai
        erreur = reponse.json()["erreur"]
        assert erreur["code"] == "CLE_INVALIDE" and erreur["requete_id"]
        assert reponse["WWW-Authenticate"] == "Bearer"


@override_settings(ROOT_URLCONF="apps.api.tests.urls_essai")
def test_une_session_ouverte_n_ouvre_pas_l_api(client, comptes):
    client.force_login(comptes["admin"])
    assert _appel(client).status_code == 401
    # Avec la clé d'un autre compte, c'est le client de la clé qui est posé,
    # jamais celui de la session.
    corps = _appel(client, comptes["autre"]).json()
    assert corps["client"] != str(comptes["faso"].id)
    assert comptes["cle"].prefixe not in corps["cles_visibles"]


@override_settings(ROOT_URLCONF="apps.api.tests.urls_essai")
def test_une_cle_revoquee_est_refusee_immediatement(client, comptes):
    assert _appel(client, comptes["jeton"]).status_code == 200
    with transaction.atomic():
        poser_client(comptes["faso"].id)
        revoquer_cle(comptes["cle"], auteur=comptes["admin"])
    # Aucune attente, aucun cache à vider : l'appel suivant est refusé.
    assert _appel(client, comptes["jeton"]).status_code == 401
    actions = Audit.objects.using("worker").values_list("action", flat=True)
    assert {"comptes.cle_api.creation", "comptes.cle_api.revocation"} <= set(actions)


def test_sans_prefixe_pose_la_table_ne_montre_rien(comptes):
    with transaction.atomic():
        assert CleApi.objects.count() == 0
        # La lecture par préfixe ne montre que la ligne de ce préfixe.
        with connections["default"].cursor() as curseur:
            curseur.execute(
                "SELECT set_config('app.cle_prefixe', %s, true)", [comptes["cle"].prefixe]
            )
        assert list(CleApi.objects.values_list("id", flat=True)) == [comptes["cle"].id]
        # Et elle n'ouvre aucune écriture.
        assert CleApi.objects.filter(id=comptes["cle"].id).update(nom="détournée") == 0


def test_le_secret_complet_n_est_stocke_nulle_part(comptes):
    secret = comptes["jeton"].split("_", 3)[3]
    with connections["worker"].cursor() as curseur:
        curseur.execute(
            "SELECT table_name, column_name FROM information_schema.columns "
            "WHERE table_schema = 'public' "
            "AND data_type IN ('text', 'character varying', 'jsonb', 'json', 'bytea')"
        )
        colonnes = curseur.fetchall()
        assert colonnes
        trouvees = []
        for table, colonne in colonnes:
            curseur.execute(
                f'SELECT count(*) FROM "{table}" WHERE "{colonne}"::text LIKE %s',
                [f"%{secret}%"],
            )
            if curseur.fetchone()[0]:
                trouvees.append((table, colonne))
    assert trouvees == []
    # Ni dans Redis, ni en clé ni en valeur.
    redis = client_redis()
    for nom in redis.scan_iter(count=1000):
        assert secret.encode() not in nom
        if redis.type(nom) == b"string":
            assert secret.encode() not in (redis.get(nom) or b"")
    # Le condensat est bien de l'Argon2id.
    cle = CleApi.objects.using("worker").get(id=comptes["cle"].id)
    assert cle.condensat.startswith("$argon2id$")


def test_la_verification_tient_sous_dix_millisecondes(comptes, espaces):
    # Cinquante clés de plus : le préfixe évite de les essayer toutes.
    espaces("worker")
    for rang in range(50):
        creer_cle(client=comptes["faso"], nom=f"Clé {rang}", auteur=None)
    espaces("client")
    durees = []
    for _ in range(20):
        with transaction.atomic():
            debut = time.perf_counter()
            assert verifier(comptes["jeton"]) is not None
            durees.append(time.perf_counter() - debut)
    assert statistics.median(durees) < 0.010, durees


def test_l_ecran_montre_la_cle_une_seule_fois(client, comptes):
    client.force_login(comptes["admin"])
    reponse = client.post("/compte/cles-api", {"nom": "Caisse", "environnement": "TEST"})
    assert reponse.status_code == 200
    assert reponse["Cache-Control"] == "no-store"
    page = reponse.content.decode()
    jeton = re.search(r"sk_test_[0-9a-f]{10}_[A-Za-z0-9_-]+", page).group(0)
    secret = jeton.split("_", 3)[3]
    # Rechargée, la page liste la clé par son préfixe, sans jamais son secret.
    page = client.get("/compte/cles-api").content.decode()
    assert "Caisse" in page and secret not in page
    cle = CleApi.objects.using("worker").get(nom="Caisse")
    assert cle.environnement == CleApi.Environnement.TEST
    assert jeton.split("_", 3)[2] == cle.prefixe
