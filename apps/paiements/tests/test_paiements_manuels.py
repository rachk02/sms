"""Paiements manuels : virement ou espèces saisis par un agent (T-87).

Sous le rôle et dans l'espace du back-office, comme l'agent qui saisit.
"""

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.models import Audit
from apps.credits import services as credits
from apps.credits.models import Ecriture
from apps.fichiers.models import Fichier
from apps.paiements import services
from apps.paiements.models import Transaction

pytestmark = pytest.mark.django_db(transaction=True)

PDF = b"%PDF-1.7\n" + b"0" * 512
VIREMENT = Transaction.Fournisseur.VIREMENT


@pytest.fixture
def agent(en_tant_que_backoffice):
    return creer_utilisateur(
        courriel="agent@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Agent",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def compte(agent):
    return creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)


def _saisir(compte, agent, **champs):
    donnees = {
        "fournisseur": VIREMENT,
        "credits_ht": 100_000,
        "reference": "VIR-2026-0042",
        "motif": "Virement reçu sur le compte Coris Bank",
        "justificatif": PDF,
        "nom_justificatif": "bordereau.pdf",
        "agent": agent,
    } | champs
    return services.saisir_manuel(compte, **donnees)


def test_un_virement_saisi_credite_avec_l_agent_et_le_motif_et_s_audite(compte, agent):
    transaction_ = _saisir(compte, agent, adresse_ip="10.1.2.3")

    assert (transaction_.etat, transaction_.credits, transaction_.montant) == (
        Transaction.Etat.CONFIRME,
        100_000,
        118_000,
    )
    assert transaction_.justificatif.famille == Fichier.Famille.PAIEMENT
    (achat,) = Ecriture.objects.filter(client_id=compte.id)
    assert (achat.type, achat.montant, achat.auteur_id) == ("ACHAT", 100_000, agent.id)
    assert achat.motif == "Virement reçu sur le compte Coris Bank"
    assert achat.reference == f"paiement:{transaction_.id}"
    assert credits.solde(compte.id) == credits.solde_recalcule(compte.id)
    (trace,) = Audit.objects.filter(action="paiements.manuel.saisi")
    assert trace.auteur_id == agent.id and trace.adresse_ip == "10.1.2.3"
    assert (trace.valeur_apres["credits"], trace.valeur_apres["montant"]) == (100_000, 118_000)


@pytest.mark.parametrize(
    "champs, message",
    [
        ({"justificatif": None}, "sans justificatif"),
        ({"justificatif": b""}, "sans justificatif"),
        ({"justificatif": b"pas un pdf"}, ""),
        ({"motif": "  "}, "motif"),
        ({"reference": ""}, "référence"),
        ({"fournisseur": Transaction.Fournisseur.ORANGE_MONEY}, "virement ou un versement"),
        ({"credits_ht": 125}, "multiples de 50"),
    ],
    ids=["sans fichier", "fichier vide", "fichier refuse", "motif", "reference", "moyen", "pas"],
)
def test_une_saisie_incomplete_ne_credite_rien(compte, agent, champs, message):
    with pytest.raises(services.SaisieRefusee) as refus:
        _saisir(compte, agent, **champs)
    assert message in str(refus.value)
    assert not Ecriture.objects.filter(client_id=compte.id).exists()
    assert not Transaction.objects.exists()


def test_le_meme_virement_ne_se_saisit_pas_deux_fois(compte, agent):
    _saisir(compte, agent)
    with pytest.raises(services.SaisieRefusee, match="déjà saisi"):
        _saisir(compte, agent)
    assert Ecriture.objects.filter(client_id=compte.id).count() == 1


def test_la_base_refuse_un_paiement_manuel_sans_justificatif(compte, agent):
    with pytest.raises(IntegrityError), transaction.atomic():
        Transaction.objects.create(
            client=compte,
            fournisseur=VIREMENT,
            reference_transaction="VIR-1",
            montant=59,
            credits=50,
            saisi_par=agent,
            motif="essai",
        )


def test_l_ecran_du_back_office_refuse_sans_justificatif_puis_enregistre(client, compte, agent):
    client.force_login(agent)
    adresse = "/admin/paiements/transaction/add/"
    assert client.get(adresse).status_code == 200
    donnees = {
        "client": str(compte.id),
        "fournisseur": VIREMENT,
        "reference": "VIR-7",
        "credits": "5000",
        "motif": "Virement reçu",
    }
    page = client.post(adresse, donnees).content.decode()
    assert "Aucune saisie manuelle sans justificatif" in page

    donnees["justificatif"] = SimpleUploadedFile("bordereau.pdf", PDF, "application/pdf")
    reponse = client.post(adresse, donnees)
    assert reponse.status_code == 302
    (achat,) = Ecriture.objects.filter(client_id=compte.id)
    assert (achat.montant, achat.auteur_id) == (5_000, agent.id)
