"""Export des données et clôture de compte (T-89).

Sous le rôle du worker, qui voit tous les comptes : l'ordonnanceur efface, le
back-office clôt et rembourse. La règle du solde est celle des conditions
générales, tranchée le 6 novembre 2026 : les envois consomment d'abord les
crédits offerts, et seule la part payée et non consommée se rembourse.
"""

from datetime import timedelta

import pytest
from django.db import transaction
from django.test import override_settings
from django.utils import timezone

from apps.backoffice.services import cloturer_compte
from apps.campagnes import services as campagnes
from apps.campagnes.models import Campagne
from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import ClotureRefusee, creer_client, creer_utilisateur
from apps.contacts.models import Contact, Liste, ListeContact, ListeNoire
from apps.contacts.services import creer_contact, effacer_comptes_clos
from apps.core.models import Audit
from apps.credits import services as credits
from apps.credits.models import CompteCredit, Ecriture
from apps.paiements import services as paiements
from apps.paiements.models import Facture, Transaction

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

EMETTEUR = {
    "raison_sociale": "Émetteur d'essai SARL",
    "rccm": "BF-OUA-ESSAI-B-0001",
    "ifu": "00000000X",
    "regime_fiscal": "Régime d'essai",
    "adresse": "01 BP 0000 Ouagadougou 01",
}


@pytest.fixture
def agent(en_tant_que_worker):
    return creer_utilisateur(
        courriel="agent@el-immeka.test", mot_de_passe="une-phrase-solide", nom="Agent"
    )


@pytest.fixture
def compte(agent):
    client = creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)
    creer_utilisateur(
        courriel="awa@fasoriz.bf", mot_de_passe="x-1234-abcd", nom="Awa", client=client
    )
    return client


def _ecrire(client, type_, montant, reference):
    with transaction.atomic():
        compte = credits._compte_verrouille(client.id)
        credits._ecrire(compte, type_, montant, reference=reference, motif="essai", auteur=None)


def _paye(client, credits_ht, reference):
    """Un achat confirmé, crédité et facturé, comme en ligne."""
    transaction_ = Transaction.objects.create(
        client=client,
        fournisseur=Transaction.Fournisseur.SIMULE,
        reference_transaction=reference,
        montant=credits_ht * 118 // 100,
        credits=credits_ht,
        etat=Transaction.Etat.CONFIRME,
        confirme_le=timezone.now(),
    )
    credits.acheter(client.id, credits_ht, reference=paiements.reference_registre(transaction_.id))
    return transaction_


def _consomme(client, montant, n=1):
    _ecrire(client, Ecriture.Type.CONSOMMATION, -montant, f"essai:{n}")


def test_les_envois_consomment_d_abord_les_credits_offerts(compte, agent):
    _paye(compte, 10_000, "P-1")
    credits.recrediter(compte.id, 2_000, motif="Geste commercial", auteur=agent)
    _consomme(compte, 1_500)
    part = credits.part_remboursable(compte.id)
    # 1 500 pris sur les 2 000 offerts : tout le payé reste remboursable.
    assert (part.solde, part.remboursable, part.perdu) == (10_500, 10_000, 500)

    _consomme(compte, 1_500, n=2)
    part = credits.part_remboursable(compte.id)
    # 3 000 consommés : 2 000 offerts, puis 1 000 payés.
    assert (part.solde, part.remboursable, part.perdu) == (9_000, 9_000, 0)

    # Un ajustement positif corrige une consommation facturée à tort.
    credits.ajuster(compte.id, 500, motif="Envoi facturé à tort", auteur=agent)
    assert credits.part_remboursable(compte.id).remboursable == 9_500


def test_la_cloture_arrete_les_envois_et_les_connexions(compte, agent):
    cloturer_compte(compte, agent=agent, motif="Lettre du 6 novembre 2026")

    compte.refresh_from_db()
    assert compte.etat == Client.Etat.CLOTURE and compte.cloture_le
    assert not Utilisateur.objects.filter(client=compte, is_active=True).exists()
    with pytest.raises(campagnes.CampagneRefusee, match="clôturé"):
        campagnes.envoyer_test(Campagne(client=compte), numero="70000000", auteur=agent)
    with pytest.raises(ClotureRefusee, match="déjà clos"):
        cloturer_compte(compte, agent=agent, motif="Encore")
    assert Audit.objects.filter(action="comptes.cloture").count() == 1


def test_le_remboursement_porte_des_avoirs_et_solde_le_compte(compte, agent):
    _paye(compte, 10_000, "P-1")
    _paye(compte, 5_000, "P-2")
    credits.recrediter(compte.id, 1_000, motif="Geste commercial", auteur=agent)
    _consomme(compte, 7_000)  # 1 000 offerts, puis 6 000 payés
    with override_settings(FACTURATION_EMETTEUR=EMETTEUR):
        with pytest.raises(paiements.RemboursementRefuse, match="clôture"):
            paiements.rembourser_cloture(compte, agent=agent, reference="VIR-R-1")
        cloturer_compte(compte, agent=agent, motif="Lettre du 6 novembre 2026")
        compte.refresh_from_db()
        with pytest.raises(paiements.RemboursementRefuse, match="ne portent pas encore"):
            paiements.rembourser_cloture(compte, agent=agent, reference="VIR-R-1")

        assert paiements.facturer_en_attente() == 2
        resultat = paiements.rembourser_cloture(compte, agent=agent, reference="VIR-R-1")

    # 9 000 payés non consommés : 5 000 sur la facture la plus récente, 4 000 sur l'autre.
    assert (resultat.credits_rembourses, resultat.credits_perdus) == (9_000, 0)
    avoirs = {
        a.origine.transaction.reference_transaction: a for a in Facture.objects.filter(type="AVOIR")
    }
    assert (avoirs["P-2"].montant_ht, avoirs["P-1"].montant_ht) == (5_000, 4_000)
    assert resultat.montant_ttc == 5_900 + 4_720
    assert credits.solde(compte.id) == credits.solde_recalcule(compte.id) == credits.Solde(0, 0)
    assert credits.part_remboursable(compte.id).remboursable == 0


def test_les_contacts_d_un_compte_clos_s_effacent_au_bout_de_trente_jours(compte, agent):
    contact = creer_contact(client=compte, numero="70 12 34 56", origine=Contact.Origine.IMPORT)
    liste = Liste.objects.create(client=compte, nom="Clients", nombre_contacts=1)
    ListeContact.objects.create(
        liste=liste, contact_id=contact.id, client=compte, ajoute_le=timezone.now()
    )
    ListeNoire.objects.create(
        client=compte, numero_normalise="+22670999999", origine=ListeNoire.Origine.STOP_SMS
    )
    _paye(compte, 1_000, "P-1")
    with override_settings(FACTURATION_EMETTEUR=EMETTEUR):
        paiements.facturer_en_attente()
    cloturer_compte(compte, agent=agent, motif="Lettre du 6 novembre 2026")
    compte.refresh_from_db()
    registre = list(Ecriture.objects.filter(client_id=compte.id).values_list("id", flat=True))

    assert effacer_comptes_clos(compte.cloture_le + timedelta(days=29)) == 0
    assert Contact.objects.filter(client=compte).exists()

    assert effacer_comptes_clos(compte.cloture_le + timedelta(days=31)) == 1
    assert not Contact.objects.filter(client=compte).exists()
    assert not Liste.objects.filter(client=compte).exists()
    # Restent : la preuve d'opposition, les pièces comptables, le registre.
    assert ListeNoire.objects.filter(client=compte).count() == 1
    assert Facture.objects.filter(client=compte).count() == 1
    assert (
        list(Ecriture.objects.filter(client_id=compte.id).values_list("id", flat=True)) == registre
    )
    assert CompteCredit.objects.filter(client_id=compte.id).exists()
    assert Audit.objects.filter(action="contacts.effacement").count() == 1
    # Rejoué, le balayage ne trouve plus rien.
    assert effacer_comptes_clos(compte.cloture_le + timedelta(days=40)) == 0
