"""Lien d'export des contacts (décision A4 du 9 novembre 2026).

Le courriel ne porte plus de lien signé de sept jours vers le stockage : il
porte un lien du portail, qui exige la connexion et contrôle les droits, puis
renvoie vers un lien signé de cinq minutes. L'export reste téléchargeable sept
jours depuis le portail, par son seul demandeur.
"""

from datetime import timedelta

import pytest
from django.conf import settings
from django.core import mail
from django.utils import timezone

from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts import recherche
from apps.contacts.models import Export
from apps.contacts.recherche import Filtres
from apps.contacts.services import creer_contact
from apps.core.models import Audit

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


def _membre(compte, courriel, role=Utilisateur.Role.UTILISATEUR):
    return creer_utilisateur(
        courriel=courriel,
        mot_de_passe="x-1234-abcd",
        nom="U",
        client=compte,
        role=role,
        courriel_verifie_le=timezone.now(),
    )


@pytest.fixture
def compte():
    return creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)


@pytest.fixture
def demandeur(compte):
    return _membre(compte, "u@exemple.bf")


@pytest.fixture
def export(espaces, compte, demandeur):
    espaces("worker")
    creer_contact(client=compte, numero="+22670000001", origine="SAISIE")
    demande = recherche.demander_export(client=compte, demandeur=demandeur, filtres=Filtres())
    recherche.produire(demande.id)
    espaces("client")
    return demande


def _adresse(export):
    return f"/contacts/exports/{export.id}"


def test_le_courriel_porte_un_lien_du_portail_et_non_du_stockage(export):
    corps = mail.outbox[-1].body
    assert f"{settings.PORTAIL_URL}{_adresse(export)}" in corps
    assert "X-Amz-Signature" not in corps
    assert "après connexion" in corps


def test_le_lien_exige_la_connexion_puis_renvoie_un_lien_de_cinq_minutes(client, export, demandeur):
    assert client.get(_adresse(export))["Location"].startswith("/connexion")
    client.force_login(demandeur)
    reponse = client.get(_adresse(export))
    assert reponse.status_code == 302
    cible = reponse["Location"]
    assert "X-Amz-Signature" in cible and "X-Amz-Expires=300" in cible
    assert (
        Audit.objects.using("worker")
        .filter(action="contacts.export.telechargement", objet_id=export.id)
        .exists()
    )


def test_ni_un_autre_membre_ni_un_autre_compte_ne_le_telecharge(client, compte, export):
    collegue = _membre(compte, "chef@exemple.bf", Utilisateur.Role.ADMIN)
    client.force_login(collegue)
    assert client.get(_adresse(export)).status_code == 404
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    client.force_login(_membre(autre, "autre@exemple.bf", Utilisateur.Role.ADMIN))
    assert client.get(_adresse(export)).status_code == 404


def test_un_export_de_plus_de_sept_jours_n_est_plus_servi(client, export, demandeur):
    Export.objects.using("worker").filter(id=export.id).update(
        termine_le=timezone.now() - timedelta(days=7, minutes=1)
    )
    client.force_login(demandeur)
    reponse = client.get(_adresse(export))
    assert reponse.status_code == 410
    assert "X-Amz" not in reponse.content.decode()
