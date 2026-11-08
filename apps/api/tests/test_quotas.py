"""Quotas à deux niveaux (T-107).

Les en-têtes annoncent le restant pour les deux niveaux. Un dépassement au
niveau compte lève une alerte à l'exploitation en plus du refus. Redis
indisponible ne fait pas tomber l'API, il fait tomber le quota en mode
permissif avec alerte.
"""

import importlib
import uuid
from datetime import datetime

import pytest
import redis
from django.test import override_settings
from django.utils import timezone

from apps.api import quotas
from apps.comptes.models import Client
from apps.comptes.services import creer_cle, creer_client
from apps.conformite import plafonds
from apps.conformite.models import AlerteFraude, RegleFraude
from apps.core.models import EvenementSortant

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

ESSAI = "/v1/essai"
MIDI = timezone.make_aware(datetime(2026, 11, 9, 12, 5))
T0 = 1_800_000_000_000


@pytest.fixture(autouse=True)
def regle(transactional_db):
    migration = importlib.import_module("apps.core.migrations.0037_regle_plafond_compte")
    RegleFraude.objects.using("worker").update_or_create(
        code=migration.REGLE["code"], defaults=migration.REGLE
    )


@pytest.fixture
def compte(espaces):
    espaces("worker")
    client = creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    Client.objects.filter(id=client.id).update(plafond_horaire=3, plafond_journalier=1000)
    client.refresh_from_db()
    _, jeton = creer_cle(client=client, nom="Site", auteur=None)
    espaces("client")
    return client, jeton


@override_settings(API_QUOTAS={"rafale_par_seconde": 3, "soutenu_par_minute": 5})
def test_le_seau_tient_la_rafale_et_le_soutenu():
    cle = uuid.uuid4()
    decisions = [quotas.prendre(cle, maintenant_ms=T0) for _ in range(4)]
    assert [d.accepte for d in decisions] == [True, True, True, False]
    assert decisions[2].entetes["Quota-Cle-Seconde-Restant"] == "0"
    assert decisions[2].entetes["Quota-Cle-Minute-Restant"] == "2"
    assert decisions[3].attente_s == 1
    # Une seconde plus tard, la rafale est pleine, mais le soutenu n'a plus
    # que deux jetons : c'est lui qui refuse le troisième.
    decisions = [quotas.prendre(cle, maintenant_ms=T0 + 1000) for _ in range(3)]
    assert [d.accepte for d in decisions] == [True, True, False]
    assert decisions[2].entetes["Quota-Cle-Minute-Restant"] == "0"
    # Un jeton soutenu revient toutes les douze secondes ; il en restait un
    # douzième, d'où onze secondes d'attente (douze à l'arrondi près).
    assert decisions[2].attente_s in (11, 12)
    assert quotas.prendre(cle, maintenant_ms=T0 + 13_000).accepte


@override_settings(
    ROOT_URLCONF="apps.api.tests.urls_essai",
    API_QUOTAS={"rafale_par_seconde": 2, "soutenu_par_minute": 100},
)
def test_les_entetes_annoncent_les_deux_niveaux_et_le_refus(client, compte):
    _, jeton = compte
    entetes = {"HTTP_AUTHORIZATION": f"Bearer {jeton}"}
    reponse = client.get(ESSAI, **entetes)
    assert reponse.status_code == 200
    assert reponse["Quota-Cle-Seconde-Limite"] == "2"
    assert reponse["Quota-Cle-Seconde-Restant"] == "1"
    assert reponse["Quota-Cle-Minute-Restant"] == "99"
    assert reponse["Quota-Compte-Heure-Limite"] == "3"
    assert reponse["Quota-Compte-Heure-Restant"] == "3"
    assert reponse["Quota-Compte-Jour-Restant"] == "1000"
    client.get(ESSAI, **entetes)
    refus = client.get(ESSAI, **entetes)
    assert refus.status_code == 429
    assert refus.json()["erreur"]["code"] == "QUOTA_CLE_DEPASSE"
    assert int(refus["Retry-After"]) >= 1
    assert refus["Quota-Cle-Seconde-Restant"] == "0"


def test_un_depassement_du_compte_leve_une_alerte_en_plus_du_refus(compte, espaces):
    client, _ = compte
    espaces("worker")
    # Un lot de cinq contre un plafond de trois : trois passent, l'alerte part.
    assert plafonds.consommer_lot(client, 5, maintenant=MIDI, cle_api="abc") == (3, "horaire")
    alerte = AlerteFraude.objects.get(client=client)
    assert alerte.gravite == AlerteFraude.Gravite.ALERTE
    assert alerte.details["cle_api"] == "abc" and alerte.details["essais"] == 5
    # Au seuil critique (200 % du plafond), l'alerte devient critique.
    assert plafonds.consommer_lot(client, 2, maintenant=MIDI) == (0, "horaire")
    assert AlerteFraude.objects.filter(
        client=client, gravite=AlerteFraude.Gravite.CRITIQUE
    ).exists()


@override_settings(ROOT_URLCONF="apps.api.tests.urls_essai")
def test_redis_indisponible_ne_fait_pas_tomber_l_api(client, compte, monkeypatch):
    _, jeton = compte
    eteint = redis.Redis(host="127.0.0.1", port=1, socket_connect_timeout=0.2)
    monkeypatch.setattr(quotas, "client_redis", lambda: eteint)
    monkeypatch.setattr(plafonds, "client_redis", lambda: eteint)
    monkeypatch.setattr(quotas, "_derniere_alerte", float("-inf"))
    reponse = client.get(ESSAI, HTTP_AUTHORIZATION=f"Bearer {jeton}")
    assert reponse.status_code == 200
    assert not any(nom.startswith("Quota-") for nom in reponse.headers)
    assert EvenementSortant.objects.using("worker").filter(nom=quotas.EVENEMENT_PERMISSIF).exists()
