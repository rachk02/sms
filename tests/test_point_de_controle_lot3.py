"""Point de contrôle du lot 3, T-42 : deux parcours complets par l'interface.

Parcours 1 : l'agent enregistre un client signé au back-office, le représentant
reçoit son invitation, crée son accès au portail, l'agent revoit le dossier et
l'approuve, le client sort du bac à sable et son premier envoi part vers un
numéro quelconque.

Parcours 2 : même début, dossier rejeté ; le compte reste en bac à sable, le
client voit le motif, et son envoi vers un numéro non vérifié est refusé.

Chaque étape passe par les écrans (back-office ou portail), dans l'espace et
sous le rôle PostgreSQL qui les servent en exploitation. Seul l'envoi passe par
le service : l'écran d'envoi arrive au lot 5. Les deux parcours laissent leur
trace au journal d'audit.
"""

import re

import pytest
from django.conf import settings
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client as Navigateur
from django.utils import timezone

from apps.backoffice.services import CONTROLES_DE_REVUE
from apps.comptes.models import Client, Dossier, Utilisateur
from apps.comptes.services import creer_utilisateur
from apps.core.etats import Etat
from apps.core.models import Audit
from apps.journal.services import envoyer_unitaire
from apps.telecom import correlation
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import passerelle_pour

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "backoffice", "worker"])

PDF = b"%PDF-1.7\n" + b"0" * 256
MOT_DE_PASSE = "une-phrase-de-passe-solide"


def _pieces():
    return {
        f"piece_{t}": SimpleUploadedFile(f"{t}.pdf", PDF, "application/pdf")
        for t in ("RCCM", "IFU", "PIECE_GERANT", "DEMANDE_ECRITE")
    }


def _enregistrer(agent_nav, rang):
    reponse = agent_nav.post(
        "/admin/comptes/client/add/",
        {
            "raison_sociale": f"Parcours {rang} SA",
            "forme": Client.Forme.SOCIETE,
            "rccm": f"BF-OUA-2026-B-9{rang:03d}",
            "ifu": f"0009{rang:04d}Z",
            "representant_nom": f"Représentant {rang}",
            "representant_courriel": f"rep{rang}@parcours.bf",
        }
        | _pieces(),
    )
    assert reponse.status_code == 302, reponse.content.decode()[:500]
    return Dossier.objects.get(client__raison_sociale=f"Parcours {rang} SA")


def _creer_acces(rang):
    invitation = next(m for m in mail.outbox if m.to == [f"rep{rang}@parcours.bf"])
    lien = re.search(r"/invitation/\S+", invitation.body).group(0)
    navigateur = Navigateur()
    reponse = navigateur.post(lien, {"mot_de_passe": MOT_DE_PASSE, "confirmation": MOT_DE_PASSE})
    assert reponse.status_code == 302
    return navigateur


def _envoyer(client_id, numero):
    connecteur = Connecteur.objects.using("worker").get_or_create(
        nom="parcours-simulateur",
        defaults={"operateur": Operateur.ORANGE, "type": Connecteur.Type.SIMULE},
    )[0]
    passerelle = passerelle_pour(connecteur)
    message = envoyer_unitaire(
        client=Client.objects.using("worker").get(id=client_id),
        numero=numero,
        texte="Premier envoi",
        expediteur=settings.SMS_NUMERO_COURT,
        passerelle=passerelle,
        cout_unitaire=10,
    )
    if message.reference_smsc:
        correlation._redis().delete(correlation.cle_redis(connecteur.id, message.reference_smsc))
    return message


@pytest.fixture
def agent(espaces):
    espaces("backoffice")
    agent = creer_utilisateur(
        courriel="agent@el-immeka.test",
        mot_de_passe=MOT_DE_PASSE,
        nom="Agent",
        is_staff=True,
        is_superuser=True,
        courriel_verifie_le=timezone.now(),
    )
    navigateur = Navigateur()
    navigateur.force_login(agent)
    return navigateur


def test_parcours_complet_avec_approbation(espaces, agent):
    dossier = _enregistrer(agent, 1)
    revue = agent.get(f"/admin/comptes/dossier/{dossier.id}/change/").content.decode()
    assert revue.count("X-Amz-Signature") == 4

    espaces("client")
    portail = _creer_acces(1)
    accueil = portail.get("/").content.decode()
    assert "Parcours 1 SA" in accueil and "bac à sable" in accueil and "déposé" in accueil

    espaces("backoffice")
    decision = agent.post(
        f"/admin/comptes/dossier/{dossier.id}/change/",
        {"approuver": "1", "controles": list(CONTROLES_DE_REVUE)},
    )
    assert decision.status_code == 302

    espaces("client")
    assert "bac à sable" not in portail.get("/").content.decode()
    espaces("worker")  # la soumission est le fait du worker d'envoi
    message = _envoyer(dossier.client_id, "+22670555444")
    assert message.etat == Etat.SOUMIS

    actions = set(
        Audit.objects.using("backoffice")
        .filter(client_id=dossier.client_id)
        .values_list("action", flat=True)
    )
    assert {"comptes.client.enregistre", "comptes.dossier.approuve"} <= actions


def test_parcours_complet_avec_rejet(espaces, agent):
    dossier = _enregistrer(agent, 2)

    espaces("client")
    portail = _creer_acces(2)

    espaces("backoffice")
    agent.post(
        f"/admin/comptes/dossier/{dossier.id}/change/",
        {
            "rejeter": "1",
            "motif": Dossier.MotifRejet.NOM_NON_CONCORDANT,
            "detail": "IFU au nom de la gérante",
        },
    )
    assert Dossier.objects.get(id=dossier.id).etat == Dossier.Etat.REJETE

    espaces("client")
    accueil = portail.get("/").content.decode()
    assert "bac à sable" in accueil and "Raison sociale non concordante" in accueil
    assert "IFU au nom de la gérante" in accueil
    assert Client.objects.using("worker").get(id=dossier.client_id).palier == "DECOUVERTE"
    espaces("worker")  # la soumission est le fait du worker d'envoi
    message = _envoyer(dossier.client_id, "+22670555444")
    assert message.etat == Etat.REJETE

    actions = set(
        Audit.objects.using("backoffice")
        .filter(client_id=dossier.client_id)
        .values_list("action", flat=True)
    )
    assert {"comptes.client.enregistre", "comptes.dossier.rejete"} <= actions
    representant = Utilisateur.objects.using("worker").get(courriel="rep2@parcours.bf")
    assert representant.courriel_verifie_le is not None
