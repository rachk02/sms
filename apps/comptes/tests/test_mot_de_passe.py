"""Mot de passe oublié et changement de mot de passe (décision A2 du 9 novembre 2026).

Oubli : un lien signé, à usage unique, valable une heure ; la réponse est la
même que l'adresse soit connue ou non ; le second facteur reste exigé ensuite.
Changement : avec l'ancien mot de passe ; les autres sessions tombent, la
session courante reste ouverte ; un courriel prévient le titulaire.
"""

import re
from datetime import timedelta

import pyotp
import pytest
from django.core import mail
from django.test import Client as Navigateur
from django.utils import timezone

from apps.comptes import jetons, second_facteur
from apps.comptes.authentification import _cle
from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.redis import client_redis

pytestmark = pytest.mark.django_db(databases=["default", "backoffice"])

COURRIEL = "awa@exemple.bf"
ANCIEN = "une-phrase-de-passe-solide"
NOUVEAU = "une-autre-phrase-bien-longue"


@pytest.fixture(autouse=True)
def courriels_immediats(monkeypatch):
    """Les courriels partent à la validation de la transaction, qui n'arrive
    jamais dans un test non transactionnel : ils partent ici sur-le-champ."""
    from apps.core import courriels

    monkeypatch.setattr(courriels.transaction, "on_commit", lambda fonction: fonction())


@pytest.fixture(autouse=True)
def compteurs_vides():
    for courriel in (COURRIEL, "inconnu@exemple.bf"):
        client_redis().delete(_cle(courriel))
    yield
    for courriel in (COURRIEL, "inconnu@exemple.bf"):
        client_redis().delete(_cle(courriel))


@pytest.fixture
def utilisateur():
    compte = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    return creer_utilisateur(
        courriel=COURRIEL,
        mot_de_passe=ANCIEN,
        nom="Awa",
        client=compte,
        role=Utilisateur.Role.UTILISATEUR,
        courriel_verifie_le=timezone.now(),
    )


def _connecter(navigateur, mot_de_passe):
    return navigateur.post("/connexion", {"courriel": COURRIEL, "mot_de_passe": mot_de_passe})


def _sans_jeton_csrf(reponse) -> str:
    return re.sub(r'"X-CSRFToken": "[^"]+"', "", reponse.content.decode())


def _lien(courriel_envoye) -> str:
    return re.search(r"https?://[^/\s]+(/mot-de-passe/reinitialiser/\S+)", courriel_envoye.body)[1]


# Mot de passe oublié ---------------------------------------------------------


def test_la_reponse_est_la_meme_que_l_adresse_soit_connue_ou_non(client, utilisateur):
    connue = client.post("/mot-de-passe/oubli", {"courriel": COURRIEL})
    inconnue = client.post("/mot-de-passe/oubli", {"courriel": "inconnu@exemple.bf"})
    assert connue.status_code == inconnue.status_code == 200
    assert _sans_jeton_csrf(connue) == _sans_jeton_csrf(inconnue)
    assert [m.to for m in mail.outbox] == [[COURRIEL]]
    assert "une heure" in mail.outbox[0].body


def test_le_lien_change_le_mot_de_passe_une_seule_fois(client, utilisateur):
    client.post("/mot-de-passe/oubli", {"courriel": COURRIEL})
    lien = _lien(mail.outbox[0])
    assert client.get(lien).status_code == 200
    reponse = client.post(lien, {"mot_de_passe": NOUVEAU, "confirmation": NOUVEAU})
    assert reponse["Location"] == "/connexion"
    utilisateur.refresh_from_db()
    assert utilisateur.check_password(NOUVEAU) and not utilisateur.check_password(ANCIEN)
    # Le titulaire est prévenu.
    assert mail.outbox[-1].to == [COURRIEL] and "modifié" in mail.outbox[-1].subject
    # Le lien a servi : il ne sert plus.
    assert client.get(lien).status_code == 404
    assert client.post(lien, {"mot_de_passe": ANCIEN, "confirmation": ANCIEN}).status_code == 404


def test_un_lien_de_plus_d_une_heure_est_expire(client, utilisateur, monkeypatch):
    client.post("/mot-de-passe/oubli", {"courriel": COURRIEL})
    lien = _lien(mail.outbox[0])
    monkeypatch.setattr(jetons, "REINITIALISATION", (jetons.REINITIALISATION[0], timedelta(0)))
    assert client.get(lien).status_code == 410


def test_les_saisies_discordantes_ou_faibles_sont_refusees(client, utilisateur):
    client.post("/mot-de-passe/oubli", {"courriel": COURRIEL})
    lien = _lien(mail.outbox[0])
    page = client.post(lien, {"mot_de_passe": NOUVEAU, "confirmation": "autre"})
    assert "ne concordent pas" in page.content.decode()
    page = client.post(lien, {"mot_de_passe": "123", "confirmation": "123"})
    assert page.status_code == 200 and "trop court" in page.content.decode()
    utilisateur.refresh_from_db()
    assert utilisateur.check_password(ANCIEN)


def test_la_reinitialisation_ferme_les_autres_sessions(client, utilisateur):
    autre = Navigateur()
    _connecter(autre, ANCIEN)
    assert autre.get("/membres").status_code in (200, 403)
    client.post("/mot-de-passe/oubli", {"courriel": COURRIEL})
    lien = _lien(mail.outbox[0])
    client.post(lien, {"mot_de_passe": NOUVEAU, "confirmation": NOUVEAU})
    assert autre.get("/membres")["Location"].startswith("/connexion")


def test_le_second_facteur_reste_exige_apres_la_reinitialisation(client, utilisateur):
    secret = second_facteur.nouveau_secret()
    second_facteur.activer(utilisateur, secret, pyotp.TOTP(secret).now())
    client.post("/mot-de-passe/oubli", {"courriel": COURRIEL})
    lien = _lien(mail.outbox[0])
    client.post(lien, {"mot_de_passe": NOUVEAU, "confirmation": NOUVEAU})
    assert "_auth_user_id" not in client.session
    _connecter(client, NOUVEAU)
    assert client.get("/membres")["Location"].startswith("/second-facteur/verifier")


def test_un_compte_retire_ne_recoit_pas_de_lien(client, utilisateur):
    Utilisateur.objects.filter(pk=utilisateur.pk).update(is_active=False)
    client.post("/mot-de-passe/oubli", {"courriel": COURRIEL})
    assert mail.outbox == []


# Changement de mot de passe --------------------------------------------------


def test_le_changement_exige_l_ancien_mot_de_passe(client, utilisateur):
    client.force_login(utilisateur)
    page = client.post(
        "/compte/mot-de-passe",
        {"ancien": "pas-le-bon", "mot_de_passe": NOUVEAU, "confirmation": NOUVEAU},
    )
    assert page.status_code == 200 and "Mot de passe actuel incorrect" in page.content.decode()
    utilisateur.refresh_from_db()
    assert utilisateur.check_password(ANCIEN)


def test_le_changement_garde_la_session_courante_et_ferme_les_autres(client, utilisateur):
    autre = Navigateur()
    _connecter(autre, ANCIEN)
    _connecter(client, ANCIEN)
    reponse = client.post(
        "/compte/mot-de-passe",
        {"ancien": ANCIEN, "mot_de_passe": NOUVEAU, "confirmation": NOUVEAU},
    )
    assert reponse.status_code == 302
    utilisateur.refresh_from_db()
    assert utilisateur.check_password(NOUVEAU)
    assert client.get("/").status_code == 200
    assert autre.get("/")["Location"].startswith("/connexion")
    assert mail.outbox[-1].to == [COURRIEL] and "modifié" in mail.outbox[-1].subject


def test_les_essais_d_ancien_mot_de_passe_comptent_dans_le_verrouillage(client, utilisateur):
    client.force_login(utilisateur)
    for _ in range(5):
        client.post(
            "/compte/mot-de-passe",
            {"ancien": "pas-le-bon", "mot_de_passe": NOUVEAU, "confirmation": NOUVEAU},
        )
    page = client.post(
        "/compte/mot-de-passe",
        {"ancien": ANCIEN, "mot_de_passe": NOUVEAU, "confirmation": NOUVEAU},
    )
    assert "Trop de tentatives" in page.content.decode()
    utilisateur.refresh_from_db()
    assert utilisateur.check_password(ANCIEN)
