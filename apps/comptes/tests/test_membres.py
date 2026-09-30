"""Invitations, vérification d'adresse (T-32) et gestion des membres (T-36)."""

import re
from datetime import timedelta
from unittest import mock

import pytest
from django.core import mail, signing
from django.utils import timezone

from apps.comptes import jetons
from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import (
    MembreRefuse,
    changer_role,
    creer_client,
    creer_utilisateur,
    envoyer_verification,
    inviter,
    retirer_membre,
)
from apps.core.models import Audit

# Transactions réelles : un courriel part au commit du service, comme en exploitation.
pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "backoffice"])

MOT_DE_PASSE = "une-phrase-de-passe-solide"


@pytest.fixture
def admin():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    return creer_utilisateur(
        courriel="awa@exemple.bf",
        mot_de_passe=MOT_DE_PASSE,
        nom="Awa",
        client=client,
        role=Utilisateur.Role.ADMIN,
        courriel_verifie_le=timezone.now(),
    )


def _lien_du_courriel(chemin):
    trouve = re.search(rf"/{chemin}/(\S+)", mail.outbox[-1].body)
    assert trouve, mail.outbox[-1].body
    return f"/{chemin}/{trouve.group(1)}"


def _inviter(admin, courriel="moussa@exemple.bf", role=Utilisateur.Role.UTILISATEUR):
    return inviter(client=admin.client, courriel=courriel, nom="Moussa", role=role, auteur=admin)


def test_une_invitation_part_par_courriel_et_cree_l_acces(client, admin):
    membre = _inviter(admin)
    assert not membre.has_usable_password() and membre.courriel_verifie_le is None
    assert "sept jours" in mail.outbox[-1].body
    lien = _lien_du_courriel("invitation")

    page = client.get(lien)
    assert page.status_code == 200 and "moussa@exemple.bf" in page.content.decode()
    assert "Exemple SA" in page.content.decode() and "Exemple SA" in mail.outbox[-1].body
    reponse = client.post(lien, {"mot_de_passe": MOT_DE_PASSE, "confirmation": MOT_DE_PASSE})
    assert reponse.status_code == 302 and reponse["Location"] == "/"
    membre.refresh_from_db()
    assert membre.check_password(MOT_DE_PASSE) and membre.courriel_verifie_le is not None


def test_un_lien_consomme_ne_fonctionne_plus(client, admin):
    _inviter(admin)
    lien = _lien_du_courriel("invitation")
    client.post(lien, {"mot_de_passe": MOT_DE_PASSE, "confirmation": MOT_DE_PASSE})
    client.logout()
    reponse = client.get(lien)
    assert reponse.status_code == 404 and "déjà servi" in reponse.content.decode()


def test_un_lien_expire_le_dit_et_propose_un_renvoi(client, admin):
    membre = _inviter(admin)
    lien = _lien_du_courriel("invitation")
    plus_tard = timezone.now() + timedelta(days=7, minutes=1)
    with mock.patch("django.core.signing.time.time", return_value=plus_tard.timestamp()):
        reponse = client.get(lien)
    assert reponse.status_code == 410
    page = reponse.content.decode()
    assert "sept jours" in page and "Renvoyer le lien" in page

    envoyes = len(mail.outbox)
    client.post("/renvoi", {"courriel": membre.courriel})
    assert len(mail.outbox) == envoyes + 1 and "/invitation/" in mail.outbox[-1].body


def test_le_renvoi_ne_revele_pas_si_l_adresse_existe(client, admin):
    envoyes = len(mail.outbox)
    reponse = client.post("/renvoi", {"courriel": "personne@exemple.bf"})
    assert "Si un accès en attente" in reponse.content.decode()
    assert len(mail.outbox) == envoyes


def test_un_lien_falsifie_est_refuse(client, admin):
    _inviter(admin)
    lien = _lien_du_courriel("invitation")
    assert client.get(lien[:-3] + "abc").status_code == 404


def test_la_verification_d_adresse_vaut_24_heures(client, admin):
    Utilisateur.objects.filter(pk=admin.pk).update(courriel_verifie_le=None)
    admin.refresh_from_db()
    envoyer_verification(admin)
    lien = _lien_du_courriel("verification")
    plus_tard = timezone.now() + timedelta(hours=24, minutes=1)
    with mock.patch("django.core.signing.time.time", return_value=plus_tard.timestamp()):
        assert client.get(lien).status_code == 410
    assert client.get(lien).status_code == 302
    admin.refresh_from_db()
    assert admin.courriel_verifie_le is not None
    assert client.get(lien).status_code == 404  # consommé


def test_un_compte_non_verifie_ne_se_connecte_pas(client, admin):
    Utilisateur.objects.filter(pk=admin.pk).update(courriel_verifie_le=None)
    reponse = client.post("/connexion", {"courriel": admin.courriel, "mot_de_passe": MOT_DE_PASSE})
    assert reponse.status_code == 200


def test_une_adresse_deja_membre_est_refusee_avec_un_message_explicite(admin):
    _inviter(admin)
    with pytest.raises(MembreRefuse, match="déjà membre de ce compte"):
        _inviter(admin)


def test_le_dernier_administrateur_ne_peut_etre_ni_retire_ni_retrograde(admin):
    with pytest.raises(MembreRefuse, match="dernier administrateur"):
        changer_role(admin, Utilisateur.Role.UTILISATEUR, auteur=admin)
    with pytest.raises(MembreRefuse, match="dernier administrateur"):
        retirer_membre(admin, auteur=admin)


def test_chaque_changement_de_role_laisse_une_ecriture_au_journal_d_audit(admin):
    membre = _inviter(admin, role=Utilisateur.Role.ADMIN)
    changer_role(membre, Utilisateur.Role.UTILISATEUR, auteur=admin)
    entree = Audit.objects.using("backoffice").get(action="comptes.role.modifie")
    assert (entree.valeur_avant, entree.valeur_apres) == (
        {"role": "ADMIN"},
        {"role": "UTILISATEUR"},
    )
    assert entree.auteur_id == admin.pk and entree.client_id == admin.client_id


def test_un_membre_retire_perd_son_acces_sans_etre_efface(client, admin):
    membre = _inviter(admin)
    retirer_membre(membre, auteur=admin)
    membre.refresh_from_db()
    assert membre.is_active is False
    assert Utilisateur.objects.filter(pk=membre.pk).exists()


def test_seul_un_administrateur_gere_les_membres(client, admin):
    membre = _inviter(admin)
    Utilisateur.objects.filter(pk=membre.pk).update(courriel_verifie_le=timezone.now())
    client.force_login(membre)
    assert client.get("/membres").status_code == 403
    assert client.post(f"/membres/{admin.pk}/retirer").status_code == 403


def test_un_administrateur_ne_touche_pas_aux_membres_d_un_autre_compte(client, admin):
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    etranger = creer_utilisateur(
        courriel="x@autre.bf",
        mot_de_passe=MOT_DE_PASSE,
        nom="X",
        client=autre,
        role=Utilisateur.Role.ADMIN,
    )
    creer_utilisateur(
        courriel="y@autre.bf",
        mot_de_passe=MOT_DE_PASSE,
        nom="Y",
        client=autre,
        role=Utilisateur.Role.ADMIN,
    )
    client.force_login(admin)
    assert client.post(f"/membres/{etranger.pk}/retirer").status_code == 404
    etranger.refresh_from_db()
    assert etranger.is_active


def test_la_page_des_membres_invite_et_liste(client, admin):
    client.force_login(admin)
    reponse = client.post(
        "/membres", {"nom": "Moussa", "courriel": "moussa@exemple.bf", "role": "UTILISATEUR"}
    )
    assert reponse.status_code == 302
    page = client.get("/membres").content.decode()
    assert "moussa@exemple.bf" in page and "invitation en attente" in page


def test_le_jeton_ne_contient_pas_le_mot_de_passe(admin):
    jeton = jetons.creer(admin)
    contenu = signing.loads(jeton, salt=jetons.INVITATION[0])
    assert set(contenu) == {"u", "e"} and admin.password not in jeton
