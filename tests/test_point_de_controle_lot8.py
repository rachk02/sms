"""Point de contrôle du lot 8 (T-104) : une journée d'exploitation simulée.

Une campagne en cours, une liaison qui tombe, une alerte anti-fraude, un ticket
ouvert depuis un message, un incident publié, un rapprochement quotidien. Chaque
événement se voit depuis les écrans du back-office, sans requête en base ; le
journal d'audit porte toutes les actions d'administration de la journée ; aucun
écran ne met plus de deux secondes à répondre.

Chaque étape tourne sous le rôle qui la porte en exploitation : le worker pour
la supervision, la détection et le rapprochement, le portail pour le client, le
back-office pour les agents.
"""

import importlib
import time

import pytest
from django.contrib.auth.models import Group
from django.test import override_settings
from django.utils import timezone

from apps.backoffice import roles, selectors
from apps.backoffice.tests.test_clients import _preparer
from apps.campagnes import diffusion
from apps.campagnes import services as campagnes
from apps.comptes.services import creer_utilisateur
from apps.conformite import services as conformite
from apps.conformite.models import AlerteFraude, RegleFraude
from apps.contacts import listes
from apps.contacts.services import creer_contact
from apps.core.models import Audit
from apps.credits import rapprochement
from apps.journal.services import enregistrer_message
from apps.support.models import Incident, Ticket
from apps.telecom import sante
from apps.telecom.models import Connecteur, IncidentLiaison, Prefixe

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

DEUX_SECONDES = 2.0


def _code(compte, numero):
    return enregistrer_message(
        client=compte,
        numero_normalise=numero,
        operateur="ORANGE",
        expediteur_libelle="FASORIZ",
        texte="Votre code de connexion : 482193",
        cout_unitaire=20,
        date_reception=timezone.now(),
    )


def _agent(courriel, role):
    agent = creer_utilisateur(
        courriel=courriel, mot_de_passe="une-phrase-solide", nom=role, is_staff=True
    )
    agent.groups.add(Group.objects.get(name=role))
    return agent


def _ecran(client, adresse, methode="get", donnees=None):
    debut = time.perf_counter()
    reponse = getattr(client, methode)(adresse, donnees)
    duree = time.perf_counter() - debut
    assert duree < DEUX_SECONDES, (adresse, duree)
    return reponse


@override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000")
def test_une_journee_d_exploitation(client, espaces, monkeypatch):
    # La profondeur des files a ses propres tests ; ici, le courtier répond vide.
    monkeypatch.setattr(selectors, "files_du_courtier", lambda: [])
    # Le matin, côté workers : un client et sa liaison, une campagne en cours.
    espaces("worker")
    migration = importlib.import_module("apps.conformite.migrations.0005_fraude")
    for regle in migration.REGLES:
        RegleFraude.objects.update_or_create(code=regle["code"], defaults=regle)
    compte = _preparer()
    connecteur = Connecteur.objects.get()
    membre = creer_utilisateur(
        courriel="awa@fasoriz.bf",
        mot_de_passe="x-1234-abcd",
        nom="Awa",
        client=compte,
        courriel_verifie_le=timezone.now(),
    )
    liste = listes.creer(client=compte, nom="Clients")
    listes.ajouter(
        liste,
        [
            creer_contact(client=compte, numero=f"7000000{rang}", origine="SAISIE").id
            for rang in range(3)
        ],
    )
    campagne = campagnes.creer(
        client=compte,
        auteur=None,
        nom="Semences de saison",
        texte="Arrivage samedi",
        listes=[liste.id],
    )
    vu = campagnes.apercu(campagne)
    campagnes.confirmer(campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout)
    diffusion.publier()

    # La liaison tombe ; la supervision l'enregistre au passage suivant.
    Connecteur.objects.filter(id=connecteur.id).update(parametres={"coupe": True})
    sante.surveiller()
    sante.surveiller()
    assert IncidentLiaison.objects.filter(connecteur=connecteur, fin__isnull=True).exists()

    # Un pic de codes vers une plage jamais servie : alerte anti-fraude.
    regle = RegleFraude.objects.get(code=RegleFraude.Code.CODES_PLAGE)
    regle.seuil_alerte, regle.seuil_critique = 3, 50
    conformite.regler_fraude(regle, agent=None)
    codes = [_code(compte, f"+2267055000{rang}") for rang in range(3)]
    assert AlerteFraude.objects.filter(client=compte).exists()

    # Côté client : un ticket ouvert depuis la fiche d'un message, sans saisie
    # de contexte.
    espaces("client")
    client.force_login(membre)
    message = codes[0]
    reponse = _ecran(
        client,
        "/support/nouveau",
        "post",
        {
            "categorie": Ticket.Categorie.NON_LIVRE,
            "objet": "Code jamais reçu",
            "corps": "Mon client n'a pas reçu son code.",
            "message": str(message.id),
            "recu": message.date_reception.isoformat(),
        },
    )
    assert reponse.status_code == 302
    client.logout()

    # Le soir, le rapprochement quotidien du registre.
    espaces("worker")
    sante.surveiller()
    assert rapprochement.rapprocher() == []

    # Côté back-office : chaque événement se voit à l'écran, et se traite.
    espaces("backoffice")
    support = _agent("support@el-immeka.test", roles.SUPPORT)
    exploitation = _agent("exploitation@el-immeka.test", roles.EXPLOITATION)

    client.force_login(exploitation)
    supervision = _ecran(client, "/admin/telecom/incidentliaison/supervision/fragment/")
    contenu = supervision.content.decode()
    ligne = next(ligne for ligne in contenu.split("<tr>") if connecteur.nom in ligne)
    assert "Déconnecté" in ligne and "tombé le" in ligne
    assert "aucun écart" in contenu
    alertes = _ecran(client, "/admin/conformite/alertefraude/").content.decode()
    assert compte.raison_sociale in alertes
    alerte = AlerteFraude.objects.get(client=compte)
    reponse = _ecran(
        client,
        f"/admin/conformite/alertefraude/{alerte.id}/traiter/",
        "post",
        {"traitement": "Client joint : campagne de codes légitime."},
    )
    assert reponse.status_code == 302
    reponse = _ecran(
        client,
        "/admin/support/incident/declarer/",
        "post",
        {
            "titre": "Orange ne livre plus",
            "composants": ["ORANGE"],
            "etat": "PANNE",
            "texte": "Liaison Orange tombée ; Moov et Telecel ne sont pas touchés.",
        },
    )
    assert reponse.status_code == 302
    assert "Orange ne livre plus" in _ecran(client, "/admin/support/incident/").content.decode()

    client.force_login(support)
    tickets = _ecran(client, "/admin/support/ticket/").content.decode()
    assert "Code jamais reçu" in tickets
    ticket = Ticket.objects.get()
    assert ticket.contexte["message_id"] == str(message.id)
    adresse = f"/admin/support/ticket/{ticket.id}/traiter/"
    # La page de l'agent montre le contexte technique emporté par le ticket.
    assert str(message.id) in _ecran(client, adresse).content.decode()
    reponse = _ecran(
        client,
        adresse,
        "post",
        {
            "action": "etat",
            "e-etat": "ESCALADE_RESEAU",
            "e-reference_reseau": "ORANGE-2026-0001",
            "e-relance_le": timezone.localdate().isoformat(),
        },
    )
    assert reponse.status_code == 302
    fiche = _ecran(client, f"/admin/comptes/client/{compte.id}/change/").content.decode()
    assert "Semences de saison" in fiche

    # Le journal d'audit porte toutes les actions d'administration de la journée.
    actions = set(
        Audit.objects.filter(auteur_id__in=[support.id, exploitation.id]).values_list(
            "action", flat=True
        )
    )
    assert {
        "conformite.fraude.traitement",
        "support.incident.declaration",
        "support.ticket.etat",
    } <= actions
    assert Incident.objects.get().etat == Incident.Etat.PANNE
    Prefixe.objects.filter(pays="BF").delete()
