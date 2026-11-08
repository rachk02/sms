"""Billetterie de support (T-102).

Un ticket ouvert depuis une fiche message porte tout son contexte technique
sans saisie. Une escalade réseau suspend le compteur d'engagement. Un ticket en
attente d'information se clôt automatiquement après sept jours, avec préavis au
cinquième.
"""

import uuid
from datetime import datetime, timedelta
from urllib.parse import urlencode

import pytest
from django.core import mail
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.etats import Etat
from apps.core.models import Audit
from apps.journal.models import Evenement, Message
from apps.journal.services import changer_etat, enregistrer_message
from apps.support import engagement, services
from apps.support.models import Ticket
from apps.telecom.models import Connecteur
from apps.telecom.supervision import publier_etat_liaisons

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

E = Ticket.Etat
C = Ticket.Categorie
LUNDI_9H = timezone.make_aware(datetime(2026, 11, 9, 9, 0))


def _heure(h, minute=0, jour=9):
    return timezone.make_aware(datetime(2026, 11, jour, h, minute))


@pytest.fixture
def compte(en_tant_que_worker):
    return creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)


@pytest.fixture
def membre(compte):
    return creer_utilisateur(
        courriel="awa@fasoriz.bf",
        mot_de_passe="x-1234-abcd",
        nom="Awa",
        client=compte,
        courriel_verifie_le=timezone.now(),
    )


@pytest.fixture
def agent(compte):
    return creer_utilisateur(
        courriel="support@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Agent",
        is_staff=True,
    )


def _ticket(compte, membre, categorie=C.ACCES_BLOQUE, maintenant=LUNDI_9H):
    return services.ouvrir(
        client=compte,
        demandeur=membre,
        categorie=categorie,
        objet="Connexion impossible",
        corps="Je ne peux plus me connecter depuis ce matin.",
        maintenant=maintenant,
    )


def _message_non_livre(client, connecteur):
    message = enregistrer_message(
        client=client,
        numero_normalise="+22670123456",
        operateur="ORANGE",
        expediteur_libelle="FASORIZ",
        texte="Votre commande est prête",
        cout_unitaire=25,
        date_reception=timezone.now() - timedelta(minutes=30),
    )
    soumis_le = timezone.now() - timedelta(minutes=29)
    for vers, champs in (
        (
            Etat.SOUMIS,
            {
                "source": Evenement.Source.WORKER,
                "horodatage": soumis_le,
                "details": {"reference_smsc": "A1B2C3"},
                "reference_smsc": "A1B2C3",
                "connecteur_id": connecteur.id,
                "date_soumission": soumis_le,
            },
        ),
        (
            Etat.NON_LIVRE,
            {
                "source": Evenement.Source.ACCUSE,
                "horodatage": soumis_le + timedelta(minutes=20),
                "details": {"libelle_operateur": "UNDELIV"},
                "date_accuse": soumis_le + timedelta(minutes=20),
            },
        ),
    ):
        from django.db import transaction

        with transaction.atomic():
            courant = Message.objects.select_for_update().get(
                id=message.id, date_reception=message.date_reception
            )
            changer_etat(courant, vers, **champs)
    return message


def test_un_ticket_ouvert_depuis_une_fiche_message_porte_son_contexte(client, membre):
    connecteur = Connecteur.objects.create(
        nom=f"orange-smpp-{uuid.uuid4().hex[:4]}",
        operateur="ORANGE",
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        debit_max=100,
    )
    publier_etat_liaisons()
    message = _message_non_livre(membre.client, connecteur)
    client.force_login(membre)
    recu = message.date_reception.isoformat()

    fiche = client.get(f"/journal/messages/{message.id}?{urlencode({'recu': recu})}")
    assert "/support/nouveau?message=" in fiche.content.decode()
    adresse = f"/support/nouveau?{urlencode({'message': message.id, 'recu': recu})}"
    page = client.get(adresse)
    assert page.status_code == 200 and connecteur.nom in page.content.decode()

    # Le client ne saisit que la catégorie, l'objet et sa demande.
    reponse = client.post(
        "/support/nouveau",
        {
            "categorie": C.NON_LIVRE,
            "objet": "Message pas arrivé",
            "corps": "Mon client dit ne rien avoir reçu.",
            "message": str(message.id),
            "recu": recu,
        },
    )
    assert reponse.status_code == 302
    ticket = Ticket.objects.get()
    assert (ticket.priorite, ticket.etat, ticket.demandeur) == ("P3", E.NOUVEAU, membre)
    contexte = ticket.contexte
    assert contexte["objet"] == "message" and contexte["message_id"] == str(message.id)
    assert (contexte["reseau"], contexte["liaison"], contexte["etat"]) == (
        "ORANGE",
        connecteur.nom,
        Etat.NON_LIVRE,
    )
    assert contexte["reference_smsc"] == "A1B2C3" and contexte["expediteur"] == "FASORIZ"
    assert contexte["date_soumission"] and contexte["date_accuse"]
    assert [m.subject for m in mail.outbox] == [f"[Ticket {ticket.reference}] Demande reçue"]


def test_le_message_d_un_autre_compte_ne_s_attache_pas(client, membre):
    autre = creer_client(raison_sociale="Banque SA", forme=Client.Forme.SOCIETE)
    etranger = enregistrer_message(
        client=autre,
        numero_normalise="+22670999999",
        operateur="ORANGE",
        expediteur_libelle="BANQUE",
        texte="Solde",
        cout_unitaire=25,
    )
    client.force_login(membre)
    reponse = client.post(
        "/support/nouveau",
        {
            "categorie": C.NON_LIVRE,
            "objet": "Pas arrivé",
            "corps": "Rien reçu.",
            "message": str(etranger.id),
            "recu": etranger.date_reception.isoformat(),
        },
    )
    assert reponse.status_code == 404 and not Ticket.objects.exists()


def test_une_escalade_reseau_suspend_le_compteur(compte, membre, agent):
    ticket = _ticket(compte, membre)
    ticket = services.prendre_en_charge(ticket, agent=agent, maintenant=_heure(10))
    assert engagement.consomme(ticket, _heure(10)) == 3600

    with pytest.raises(services.TicketRefuse, match="référence"):
        services.changer_etat(ticket, E.ESCALADE_RESEAU, agent=agent, maintenant=_heure(10))
    ticket = services.changer_etat(
        ticket,
        E.ESCALADE_RESEAU,
        agent=agent,
        reference_reseau="ORANGE-2026-4411",
        relance_le=_heure(9, jour=12),
        maintenant=_heure(10),
    )
    assert ticket.engagement_depuis is None
    # Cinq heures chez l'opérateur ne comptent pas.
    assert engagement.consomme(ticket, _heure(15)) == 3600

    ticket = services.changer_etat(ticket, E.PRIS_EN_CHARGE, agent=agent, maintenant=_heure(15))
    assert engagement.consomme(ticket, _heure(16)) == 2 * 3600
    assert ticket.reference_reseau == "ORANGE-2026-4411" and ticket.relance_le is None
    assert Audit.objects.filter(action="support.ticket.etat").count() == 2


def test_l_attente_du_client_previent_au_cinquieme_jour_et_clot_au_septieme(compte, membre, agent):
    ticket = _ticket(compte, membre)
    ticket = services.repondre_agent(
        ticket,
        agent=agent,
        corps="Depuis quel navigateur ?",
        attendre_client=True,
        maintenant=LUNDI_9H,
    )
    assert ticket.etat == E.ATTENTE_CLIENT and ticket.premiere_reponse_le == LUNDI_9H
    assert ticket.engagement_depuis is None
    mail.outbox.clear()

    assert services.traiter_echeances(LUNDI_9H + timedelta(days=4))["preavis"] == 0
    cinquieme = LUNDI_9H + timedelta(days=5, minutes=1)
    assert services.traiter_echeances(cinquieme)["preavis"] == 1
    # Rejouée, l'échéance ne prévient pas deux fois.
    assert services.traiter_echeances(cinquieme)["preavis"] == 0
    assert [m.subject.split("] ")[1] for m in mail.outbox] == ["Votre réponse est attendue"]

    faits = services.traiter_echeances(LUNDI_9H + timedelta(days=7))
    assert faits["clos_sans_reponse"] == 1
    ticket.refresh_from_db()
    assert ticket.etat == E.CLOS and ticket.clos_le == LUNDI_9H + timedelta(days=7)
    assert mail.outbox[-1].subject.endswith("Ticket clos")


def test_un_ticket_resolu_se_clot_apres_quarante_huit_heures(compte, membre, agent):
    ticket = _ticket(compte, membre)
    with pytest.raises(services.TicketRefuse, match="résolution"):
        services.changer_etat(ticket, E.RESOLU, agent=agent, maintenant=LUNDI_9H)
    services.changer_etat(
        ticket, E.RESOLU, agent=agent, resolution="Mot de passe réinitialisé.", maintenant=LUNDI_9H
    )
    assert services.traiter_echeances(LUNDI_9H + timedelta(hours=47))["clos_resolus"] == 0
    assert services.traiter_echeances(LUNDI_9H + timedelta(hours=48))["clos_resolus"] == 1
    ticket.refresh_from_db()
    assert ticket.etat == E.CLOS


def _clore(ticket, agent, quand):
    services.changer_etat(ticket, E.RESOLU, agent=agent, resolution="Réglé.", maintenant=quand)
    services.traiter_echeances(quand + timedelta(hours=48))
    ticket.refresh_from_db()
    return ticket


def test_une_reponse_rouvre_un_ticket_clos_depuis_moins_de_sept_jours(compte, membre, agent):
    ticket = _clore(_ticket(compte, membre), agent, LUNDI_9H)
    recu = services.repondre_client(
        ticket,
        auteur=membre,
        corps="Le problème revient.",
        maintenant=ticket.clos_le + timedelta(days=6),
    )
    assert recu.id == ticket.id
    assert (recu.etat, recu.clos_le, recu.resolu_le) == (E.PRIS_EN_CHARGE, None, None)
    assert recu.messages.count() == 2

    ticket = _clore(recu, agent, LUNDI_9H + timedelta(days=10))
    suite = services.repondre_client(
        ticket,
        auteur=membre,
        corps="Encore ce souci.",
        maintenant=ticket.clos_le + timedelta(days=8),
    )
    assert suite.id != ticket.id and suite.precedent_id == ticket.id
    assert suite.categorie == ticket.categorie and suite.contexte == ticket.contexte
    assert ticket.reference in suite.objet
    ticket.refresh_from_db()
    assert ticket.etat == E.CLOS


def test_une_note_interne_n_est_jamais_montree_au_client(client, compte, membre, agent):
    ticket = _ticket(compte, membre)
    services.repondre_agent(ticket, agent=agent, corps="Compte à surveiller de près", interne=True)
    services.repondre_agent(ticket, agent=agent, corps="Nous regardons votre accès.")
    client.force_login(membre)
    for adresse in (f"/support/{ticket.id}", f"/support/{ticket.id}/fragments/fil"):
        page = client.get(adresse).content.decode()
        assert "Nous regardons votre accès." in page
        assert "surveiller de près" not in page
    assert all("surveiller de près" not in m.body for m in mail.outbox)
    # Une note interne ne compte pas comme première réponse ; la réponse publique, si.
    ticket.refresh_from_db()
    assert ticket.premiere_reponse_le is not None and ticket.etat == E.PRIS_EN_CHARGE


def test_le_cycle_de_vie_refuse_les_sauts(compte, membre, agent):
    ticket = _ticket(compte, membre)
    with pytest.raises(services.TicketRefuse, match="ne passe pas"):
        services.changer_etat(ticket, E.CLOS, agent=agent)


def test_la_categorie_corrigee_refixe_la_priorite_et_se_trace(compte, membre, agent):
    ticket = _ticket(compte, membre, categorie=C.USAGE)
    assert ticket.priorite == "P4"
    ticket = services.recategoriser(ticket, C.SERVICE_INDISPONIBLE, agent=agent)
    assert ticket.priorite == "P1"
    trace = Audit.objects.get(action="support.ticket.categorie")
    assert trace.valeur_avant == {"categorie": "USAGE", "priorite": "P4"}


def test_la_liste_du_client_ne_montre_que_son_compte(client, compte, membre):
    _ticket(compte, membre)
    autre = creer_client(raison_sociale="Banque SA", forme=Client.Forme.SOCIETE)
    voisin = creer_utilisateur(
        courriel="ali@banque.bf",
        mot_de_passe="x-1234-abcd",
        nom="Ali",
        client=autre,
        courriel_verifie_le=timezone.now(),
    )
    etranger = services.ouvrir(
        client=autre, demandeur=voisin, categorie=C.USAGE, objet="Question", corps="Bonjour."
    )
    client.force_login(membre)
    page = client.get("/support").content.decode()
    assert "Connexion impossible" in page and "Question" not in page
    assert client.get(f"/support/{etranger.id}").status_code == 404
