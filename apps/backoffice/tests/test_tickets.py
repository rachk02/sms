"""Billetterie au back-office (T-102) : traitement d'un ticket et ressaisie
d'une demande arrivée par un autre canal."""

import pytest
from django.contrib.auth.models import Group
from django.utils import timezone

from apps.backoffice import roles
from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.models import Audit
from apps.support import services
from apps.support.models import MessageTicket, Ticket

pytestmark = pytest.mark.django_db(transaction=True)


def _agent(courriel, role):
    agent = creer_utilisateur(
        courriel=courriel, mot_de_passe="une-phrase-solide", nom=role, is_staff=True
    )
    agent.groups.add(Group.objects.get(name=role))
    return agent


@pytest.fixture
def dossier(en_tant_que_backoffice):
    compte = creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)
    membre = creer_utilisateur(
        courriel="awa@fasoriz.bf",
        mot_de_passe="x-1234-abcd",
        nom="Awa",
        client=compte,
        courriel_verifie_le=timezone.now(),
    )
    ticket = services.ouvrir(
        client=compte,
        demandeur=membre,
        categorie=Ticket.Categorie.ACHEMINEMENT,
        objet="Moov ne livre plus",
        corps="Depuis midi, rien ne part vers Moov.",
    )
    return compte, membre, ticket


def test_l_agent_repond_escalade_et_note(client, dossier):
    compte, _, ticket = dossier
    support = _agent("support@el-immeka.test", roles.SUPPORT)
    finance = _agent("finance@el-immeka.test", roles.FINANCE)
    adresse = f"/admin/support/ticket/{ticket.id}/traiter/"

    client.force_login(finance)
    assert client.get(adresse).status_code == 403
    client.force_login(support)
    page = client.get(adresse)
    assert page.status_code == 200 and "Moov ne livre plus" in page.content.decode()

    reponse = client.post(
        adresse, {"action": "repondre", "r-corps": "Nous vérifions la liaison Moov."}
    )
    assert reponse.status_code == 302
    client.post(adresse, {"action": "repondre", "r-corps": "Voir avec N2", "r-interne": "on"})
    assert list(MessageTicket.objects.filter(ticket=ticket).values_list("interne", flat=True)) == [
        False,
        False,
        True,
    ]

    # L'escalade à l'opérateur exige son dossier et sa relance.
    refus = client.post(adresse, {"action": "etat", "e-etat": "ESCALADE_RESEAU"})
    assert refus.status_code == 200 and "dossier" in refus.content.decode()
    reponse = client.post(
        adresse,
        {
            "action": "etat",
            "e-etat": "ESCALADE_RESEAU",
            "e-reference_reseau": "MOOV-778",
            "e-relance_le": "2026-11-12",
        },
    )
    assert reponse.status_code == 302
    ticket.refresh_from_db()
    assert (ticket.etat, ticket.reference_reseau, ticket.agent) == (
        Ticket.Etat.ESCALADE_RESEAU,
        "MOOV-778",
        support,
    )
    assert ticket.engagement_depuis is None and ticket.premiere_reponse_le is not None
    assert client.get("/admin/support/ticket/").status_code == 200


def test_une_demande_par_telephone_se_ressaisit_en_ticket(client, dossier):
    compte, membre, _ = dossier
    support = _agent("support@el-immeka.test", roles.SUPPORT)
    exploitation = _agent("exploitation@el-immeka.test", roles.EXPLOITATION)
    client.force_login(exploitation)
    assert client.get("/admin/support/ticket/saisir/").status_code == 403

    client.force_login(support)
    reponse = client.post(
        "/admin/support/ticket/saisir/",
        {
            "client": str(compte.id),
            "demandeur": "awa@fasoriz.bf",
            "canal": Ticket.Canal.TELEPHONE,
            "categorie": Ticket.Categorie.ACCES_BLOQUE,
            "objet": "Second facteur perdu",
            "corps": "Appel de Awa : téléphone perdu, plus de code.",
        },
    )
    assert reponse.status_code == 302
    ticket = Ticket.objects.get(canal=Ticket.Canal.TELEPHONE)
    assert (ticket.demandeur, ticket.priorite) == (membre, "P2")
    assert ticket.messages.get().auteur == support
    trace = Audit.objects.get(action="support.ticket.saisie")
    assert trace.auteur == support and trace.valeur_apres["canal"] == "TELEPHONE"

    # Un courriel qui n'est pas celui d'un membre du compte est refusé.
    reponse = client.post(
        "/admin/support/ticket/saisir/",
        {
            "client": str(compte.id),
            "demandeur": "inconnu@ailleurs.bf",
            "canal": Ticket.Canal.TELEPHONE,
            "categorie": Ticket.Categorie.USAGE,
            "objet": "Question",
            "corps": "Comment segmenter ?",
        },
    )
    assert reponse.status_code == 200
    assert Ticket.objects.filter(canal=Ticket.Canal.TELEPHONE).count() == 1
