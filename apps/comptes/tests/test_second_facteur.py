"""Second facteur, T-34."""

import re
import time
from datetime import timedelta

import pyotp
import pytest
from django.core import mail
from django.utils import timezone

from apps.comptes import second_facteur
from apps.comptes.middleware import CLE_CODES
from apps.comptes.models import Client, CodeSecours, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.chiffrement import dechiffrer
from apps.core.models import Audit

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "backoffice"])

MOT_DE_PASSE = "une-phrase-de-passe-solide"


def _utilisateur(role=Utilisateur.Role.ADMIN, anciennete=timedelta(0)):
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    utilisateur = creer_utilisateur(
        courriel="awa@exemple.bf",
        mot_de_passe=MOT_DE_PASSE,
        nom="Awa",
        client=client,
        role=role,
        courriel_verifie_le=timezone.now(),
    )
    if anciennete:
        Utilisateur.objects.filter(pk=utilisateur.pk).update(cree_le=timezone.now() - anciennete)
        utilisateur.refresh_from_db()
    return utilisateur


def _secret_de_la_page(page: str) -> str:
    trouve = re.search(r'tracking-wider">([A-Z2-7 ]+)</p>', page)
    return trouve.group(1).replace(" ", "")


def _activer(client):
    page = client.get("/second-facteur/activer").content.decode()
    assert "<svg" in page  # le code QR
    secret = _secret_de_la_page(page)
    client.post("/second-facteur/activer", {"code": pyotp.TOTP(secret).now()})
    return secret


def _connecter(client):
    return client.post("/connexion", {"courriel": "awa@exemple.bf", "mot_de_passe": MOT_DE_PASSE})


def test_l_activation_chiffre_le_secret_et_montre_les_codes_une_fois(client):
    utilisateur = _utilisateur()
    client.force_login(utilisateur)
    secret = _activer(client)
    utilisateur.refresh_from_db()
    assert utilisateur.second_facteur_actif
    assert secret not in utilisateur.second_facteur_secret
    assert dechiffrer(utilisateur.second_facteur_secret) == secret

    page = client.get("/second-facteur/codes").content.decode()
    codes = re.findall(r"[A-Z2-9]{4}-[A-Z2-9]{4}", page)
    assert len(codes) == 10 and CodeSecours.objects.filter(utilisateur=utilisateur).count() == 10
    assert all(code not in c.condensat for c in CodeSecours.objects.all() for code in codes)

    # Tant que la notation n'est pas confirmée, toute page ramène aux codes.
    assert client.get("/")["Location"] == "/second-facteur/codes"
    assert "Confirmez" in client.post("/second-facteur/codes", {}).content.decode()
    client.post("/second-facteur/codes", {"notes": "oui"})
    assert client.get("/").status_code == 200
    assert client.get("/second-facteur/codes")["Location"] == "/"  # plus jamais affichés


def test_un_code_faux_ne_valide_pas_l_activation(client):
    client.force_login(_utilisateur())
    client.get("/second-facteur/activer")
    reponse = client.post("/second-facteur/activer", {"code": "000000"})
    assert "Code invalide" in reponse.content.decode()
    assert not Utilisateur.objects.get(courriel="awa@exemple.bf").second_facteur_actif


def test_la_connexion_exige_le_code_apres_le_mot_de_passe(client):
    utilisateur = _utilisateur()
    client.force_login(utilisateur)
    secret = _activer(client)
    client.post("/second-facteur/codes", {"notes": "oui"})
    client.logout()

    _connecter(client)
    reponse = client.get("/membres")
    assert reponse["Location"].startswith("/second-facteur/verifier?next=/membres")
    assert (
        "Code refusé"
        in client.post("/second-facteur/verifier", {"code": "000000"}).content.decode()
    )
    # Le code de l'activation est déjà servi : c'est le suivant qui est présenté,
    # accepté dans la tolérance d'un pas d'horloge.
    suivant = pyotp.TOTP(secret).at(time.time() + 30)
    reponse = client.post("/second-facteur/verifier", {"code": suivant, "next": "/membres"})
    assert reponse["Location"] == "/membres"
    assert client.get("/membres").status_code == 200


def test_un_code_d_application_ne_sert_pas_deux_fois(client):
    utilisateur = _utilisateur()
    secret = second_facteur.nouveau_secret()
    # Un seul code lu : deux lectures peuvent tomber de part et d'autre d'une fenêtre de 30 s.
    code = pyotp.TOTP(secret).now()
    second_facteur.activer(utilisateur, secret, code)
    # Le code d'activation lui-même ne rouvre pas une session.
    assert second_facteur.verifier(utilisateur, code) is False


def test_un_code_de_secours_consomme_ne_fonctionne_plus(client):
    utilisateur = _utilisateur()
    secret = second_facteur.nouveau_secret()
    codes = second_facteur.activer(utilisateur, secret, pyotp.TOTP(secret).now())
    assert second_facteur.verifier(utilisateur, codes[0].lower()) is True
    assert second_facteur.verifier(utilisateur, codes[0]) is False
    assert second_facteur.verifier(utilisateur, codes[1].replace("-", "")) is True


def test_un_administrateur_ne_se_connecte_plus_sans_second_facteur_apres_le_delai(client):
    _utilisateur(anciennete=second_facteur.DELAI_DE_GRACE + timedelta(hours=1))
    _connecter(client)
    assert client.get("/")["Location"] == "/second-facteur/activer"
    assert client.get("/membres")["Location"] == "/second-facteur/activer"


def test_pendant_le_delai_de_grace_l_administrateur_est_prevenu(client):
    _utilisateur(anciennete=timedelta(days=2))
    _connecter(client)
    page = client.get("/").content.decode()
    assert "exige un second facteur" in page


def test_un_simple_utilisateur_n_est_pas_contraint(client):
    _utilisateur(role=Utilisateur.Role.UTILISATEUR, anciennete=timedelta(days=30))
    _connecter(client)
    assert client.get("/").status_code == 200


def test_trop_de_codes_refuses_deconnectent(client):
    utilisateur = _utilisateur()
    secret = second_facteur.nouveau_secret()
    second_facteur.activer(utilisateur, secret, pyotp.TOTP(secret).now())
    _connecter(client)
    for _ in range(5):
        reponse = client.post("/second-facteur/verifier", {"code": "000000"})
    assert reponse["Location"] == "/connexion"
    assert "_auth_user_id" not in client.session


def test_la_reinitialisation_exige_identite_et_motif_et_laisse_une_trace(en_tant_que_backoffice):
    utilisateur = _utilisateur()
    secret = second_facteur.nouveau_secret()
    second_facteur.activer(utilisateur, secret, pyotp.TOTP(secret).now())
    agent = creer_utilisateur(
        courriel="agent@el-immeka.test", mot_de_passe=MOT_DE_PASSE, nom="Agent", is_staff=True
    )
    with pytest.raises(second_facteur.CodeRefuse, match="canal indépendant"):
        second_facteur.reinitialiser(utilisateur, agent=agent, motif="x", identite_verifiee=False)
    with pytest.raises(second_facteur.CodeRefuse, match="motif"):
        second_facteur.reinitialiser(utilisateur, agent=agent, motif=" ", identite_verifiee=True)

    second_facteur.reinitialiser(
        utilisateur, agent=agent, motif="Rappel au numéro du compte", identite_verifiee=True
    )
    utilisateur.refresh_from_db()
    assert not utilisateur.second_facteur_actif and utilisateur.second_facteur_secret == ""
    assert not CodeSecours.objects.filter(utilisateur=utilisateur).exists()
    entree = Audit.objects.get(action="comptes.second_facteur.reinitialise")
    assert entree.auteur_id == agent.pk and entree.motif == "Rappel au numéro du compte"
    assert mail.outbox[-1].to == ["awa@exemple.bf"]


def test_le_back_office_impose_aussi_le_second_facteur_aux_agents(client, en_tant_que_backoffice):
    agent = creer_utilisateur(
        courriel="agent@el-immeka.test",
        mot_de_passe=MOT_DE_PASSE,
        nom="Agent",
        is_staff=True,
        is_superuser=True,
    )
    Utilisateur.objects.filter(pk=agent.pk).update(
        cree_le=timezone.now() - second_facteur.DELAI_DE_GRACE - timedelta(hours=1)
    )
    client.force_login(Utilisateur.objects.get(pk=agent.pk))
    assert client.get("/admin/")["Location"] == "/second-facteur/activer"


def test_la_cle_des_codes_a_noter_ne_reste_pas_en_session_apres_confirmation(client):
    client.force_login(_utilisateur())
    _activer(client)
    client.post("/second-facteur/codes", {"notes": "oui"})
    assert CLE_CODES not in client.session
