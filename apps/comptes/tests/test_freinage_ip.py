"""Freinage par adresse IP à la connexion (décision A3 du 9 novembre 2026).

Vingt échecs en quinze minutes depuis une même adresse la bloquent quinze
minutes, quel que soit le courriel essayé : le verrouillage par courriel
n'arrête pas celui qui essaie un mot de passe sur mille adresses. L'adresse
source se lit comme pour les rappels de paiement (T-84).
"""

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.comptes.authentification import MAX_ECHECS_IP, _cle, _cle_ip
from apps.comptes.models import Client, Connexion, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.redis import client_redis

pytestmark = pytest.mark.django_db(databases=["default", "backoffice"])

COURRIEL = "awa@exemple.bf"
MOT_DE_PASSE = "une-phrase-de-passe-solide"
ATTAQUANT, VOISIN, FRONTAL = "203.0.113.7", "203.0.113.8", "10.0.0.2"
SONDES = [f"sonde{i}@exemple.bf" for i in range(MAX_ECHECS_IP + 2)]


def _nettoyer():
    redis = client_redis()
    for adresse in (ATTAQUANT, VOISIN, FRONTAL):
        redis.delete(_cle_ip(adresse))
    for courriel in (COURRIEL, *SONDES):
        redis.delete(_cle(courriel))


@pytest.fixture(autouse=True)
def compteurs_vides():
    _nettoyer()
    yield
    _nettoyer()


@pytest.fixture
def utilisateur():
    compte = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    return creer_utilisateur(
        courriel=COURRIEL,
        mot_de_passe=MOT_DE_PASSE,
        nom="Awa",
        client=compte,
        role=Utilisateur.Role.UTILISATEUR,
        courriel_verifie_le=timezone.now(),
    )


def _essayer(client, courriel, mot_de_passe="pas-le-bon", **meta):
    meta.setdefault("REMOTE_ADDR", ATTAQUANT)
    return client.post("/connexion", {"courriel": courriel, "mot_de_passe": mot_de_passe}, **meta)


def _sonder(client, nombre, **meta):
    for courriel in SONDES[:nombre]:
        _essayer(client, courriel, **meta)


# Transactions réelles : le journal des connexions se lit par une autre connexion.
@pytest.mark.django_db(transaction=True, databases=["default", "backoffice"])
def test_vingt_echecs_depuis_une_adresse_la_bloquent(client, utilisateur):
    _sonder(client, MAX_ECHECS_IP - 1)
    # Sous le seuil, le bon mot de passe passe encore.
    assert _essayer(client, COURRIEL, MOT_DE_PASSE).status_code == 302
    client.logout()
    _sonder(client, MAX_ECHECS_IP)
    page = _essayer(client, COURRIEL, MOT_DE_PASSE)
    assert page.status_code == 200 and "Trop de tentatives" in page.content.decode()
    assert "_auth_user_id" not in client.session
    journal = Connexion.objects.using("backoffice")
    assert journal.filter(courriel_saisi=COURRIEL, motif="IP_FREINEE").exists()
    # Le compte lui-même n'est pas verrouillé : une autre adresse se connecte.
    assert _essayer(client, COURRIEL, MOT_DE_PASSE, REMOTE_ADDR=VOISIN).status_code == 302


def test_le_blocage_dure_quinze_minutes(client):
    _sonder(client, MAX_ECHECS_IP)
    assert 14 * 60 < client_redis().ttl(_cle_ip(ATTAQUANT)) <= 15 * 60


def test_une_reussite_ne_remet_pas_le_compteur_a_zero(client, utilisateur):
    _sonder(client, MAX_ECHECS_IP - 1)
    assert _essayer(client, COURRIEL, MOT_DE_PASSE).status_code == 302
    client.logout()
    _sonder(client, 1)
    assert "Trop de tentatives" in _essayer(client, COURRIEL, MOT_DE_PASSE).content.decode()


def test_un_en_tete_transmis_par_un_inconnu_ne_vaut_rien(client, utilisateur):
    # Sans frontal de confiance, changer d'en-tête ne change pas d'adresse.
    for i, courriel in enumerate(SONDES[:MAX_ECHECS_IP]):
        _essayer(client, courriel, HTTP_X_FORWARDED_FOR=f"198.51.100.{i}")
    assert "Trop de tentatives" in _essayer(client, COURRIEL, MOT_DE_PASSE).content.decode()


@override_settings(PROXIES_DE_CONFIANCE=[FRONTAL])
def test_derriere_le_frontal_c_est_l_adresse_transmise_qui_compte(client, utilisateur):
    _sonder(client, MAX_ECHECS_IP, REMOTE_ADDR=FRONTAL, HTTP_X_FORWARDED_FOR=ATTAQUANT)
    bloque = _essayer(
        client, COURRIEL, MOT_DE_PASSE, REMOTE_ADDR=FRONTAL, HTTP_X_FORWARDED_FOR=ATTAQUANT
    )
    assert "Trop de tentatives" in bloque.content.decode()
    voisin = _essayer(
        client, COURRIEL, MOT_DE_PASSE, REMOTE_ADDR=FRONTAL, HTTP_X_FORWARDED_FOR=VOISIN
    )
    assert voisin.status_code == 302
