"""Facturation des achats de crédits (T-88).

Sous le rôle du worker, comme l'ordonnanceur qui émet. Les mentions de
l'émetteur sont des valeurs d'essai : les vraies ne s'inventent pas.
"""

import io
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from django.core.management import call_command
from django.db import DatabaseError, connection, transaction
from django.test import override_settings
from django.utils import timezone
from pypdf import PdfReader

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.models import Audit
from apps.paiements import rendu, services
from apps.paiements.models import Facture, Transaction

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

EMETTEUR = {
    "raison_sociale": "Émetteur d'essai SARL",
    "rccm": "BF-OUA-ESSAI-B-0001",
    "ifu": "00000000X",
    "regime_fiscal": "Régime d'essai",
    "adresse": "01 BP 0000 Ouagadougou 01",
}
LE = datetime(2026, 11, 6, 10, 0, tzinfo=ZoneInfo("Africa/Ouagadougou"))


@pytest.fixture
def emetteur():
    with override_settings(FACTURATION_EMETTEUR=EMETTEUR):
        yield


@pytest.fixture
def compte(en_tant_que_worker):
    return creer_client(
        raison_sociale="L’Œuvre des Bénévoles",
        forme=Client.Forme.SOCIETE,
        rccm="BF-OUA-2026-B-0042",
        ifu="00042424B",
    )


def _achat(compte, reference="VIR-1", credits=10_000, montant=11_800):
    return Transaction.objects.create(
        client=compte,
        fournisseur=Transaction.Fournisseur.SIMULE,
        reference_transaction=reference,
        montant=montant,
        credits=credits,
        etat=Transaction.Etat.CONFIRME,
        confirme_le=LE,
    )


def _texte(pdf: bytes) -> str:
    return "\n".join(p.extract_text() for p in PdfReader(io.BytesIO(pdf)).pages)


def test_sans_identite_de_l_emetteur_aucune_facture_mais_le_credit_reste(compte, caplog):
    _achat(compte)
    assert services.facturer_en_attente() == 0
    assert not Facture.objects.exists()
    with pytest.raises(services.FacturationImpossible, match="incomplète"):
        services.emettre_facture(Transaction.objects.get().id)


def test_la_facture_porte_toutes_les_mentions_dans_son_pdf(compte, emetteur):
    transaction_ = _achat(compte)
    assert services.facturer_en_attente() == 1
    facture = Facture.objects.get()
    numero = f"F-{timezone.localtime().year}-000001"
    assert facture.numero == numero
    assert (facture.montant_ht, facture.taux_tva, facture.tva, facture.montant_ttc) == (
        10_000,
        18,
        1_800,
        11_800,
    )
    assert facture.fichier_id  # archivé dans le même balayage

    texte = _texte(rendu.facture_pdf(facture.mentions))
    for mention in (
        *EMETTEUR.values(),
        f"Facture n° {numero}",
        f"Émise le {timezone.localtime(facture.emise_le):%d/%m/%Y}",
        "L’Œuvre des Bénévoles",
        "RCCM : BF-OUA-2026-B-0042",
        "IFU : 00042424B",
        "Crédits SMS prépayés",
        "Total hors taxes",
        "10 000 FCFA",
        "TVA 18 %",
        "1 800 FCFA",
        "Total toutes taxes comprises",
        "11 800 FCFA",
        f"Facture acquittée : payée le 06/11/2026 par Fournisseur simulé, référence "
        f"{transaction_.reference_transaction}",
    ):
        assert mention in texte, mention

    # Relancé, le balayage ne refait ni la facture ni le PDF.
    assert services.facturer_en_attente() == 0
    assert Facture.objects.count() == 1


def test_sans_tva_la_facture_le_dit(compte, emetteur):
    facture = services.emettre_facture(_achat(compte, credits=125, montant=125).id)
    assert (facture.taux_tva, facture.tva, facture.montant_ttc) == (0, 0, 125)
    assert "TVA non appliquée" in _texte(rendu.facture_pdf(facture.mentions))


@pytest.mark.parametrize("essai", range(2))
def test_cinq_emissions_simultanees_donnent_un_a_cinq(compte, emetteur, essai):
    """Cinq fils, cinq connexions, cinq transactions réelles : ni trou ni doublon."""
    achats = [_achat(compte, reference=f"VIR-{n}").id for n in range(5)]
    depart, erreurs = threading.Barrier(5), []

    def emettre(transaction_id):
        try:
            depart.wait(5)
            services.emettre_facture(transaction_id, maintenant=LE)
        except Exception as erreur:  # noqa: BLE001
            erreurs.append(erreur)
        finally:
            connection.close()

    fils = [threading.Thread(target=emettre, args=(a,)) for a in achats]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(30)

    assert not erreurs
    assert sorted(Facture.objects.values_list("sequence", flat=True)) == [1, 2, 3, 4, 5]


def test_une_emission_qui_echoue_rend_son_numero(compte, emetteur):
    premier, second = _achat(compte, "VIR-1"), _achat(compte, "VIR-2")
    with pytest.raises(RuntimeError), transaction.atomic():
        services.emettre_facture(premier.id, maintenant=LE)
        raise RuntimeError("panne après l'attribution du numéro")
    assert services.emettre_facture(second.id, maintenant=LE).numero == "F-2026-000001"


def test_une_facture_emise_ne_se_modifie_ni_ne_se_supprime(compte, emetteur):
    facture = services.emettre_facture(_achat(compte).id, maintenant=LE)
    for ecrire in (
        lambda: Facture.objects.filter(id=facture.id).update(montant_ht=1, montant_ttc=1801),
        lambda: Facture.objects.filter(id=facture.id).delete(),
    ):
        with pytest.raises(DatabaseError), transaction.atomic():
            ecrire()
    services.archiver(facture)
    facture.refresh_from_db()
    # Le PDF se rattache une fois, jamais deux.
    with pytest.raises(DatabaseError), transaction.atomic():
        Facture.objects.filter(id=facture.id).update(fichier=None)
    assert Facture.objects.get().montant_ht == 10_000


def test_la_correction_passe_par_des_avoirs_bornes_a_la_facture(compte, emetteur):
    from apps.comptes.services import creer_utilisateur

    agent = creer_utilisateur(
        courriel="agent@el-immeka.test", mot_de_passe="une-phrase-solide", nom="Agent"
    )
    facture = services.emettre_facture(_achat(compte).id, maintenant=LE)

    premier = services.emettre_avoir(
        facture.id, montant_ht=5_000, motif="Remboursement", agent=agent
    )
    assert (premier.numero, premier.tva, premier.montant_ttc) == ("AV-2026-000001", 900, 5_900)
    with pytest.raises(services.FacturationImpossible, match="multiples de 50"):
        services.emettre_avoir(facture.id, montant_ht=125, motif="x", agent=agent)
    solde = services.emettre_avoir(facture.id, montant_ht=5_000, motif="Solde", agent=agent)
    assert solde.tva + premier.tva == facture.tva
    with pytest.raises(services.FacturationImpossible, match="trop élevé"):
        services.emettre_avoir(facture.id, montant_ht=50, motif="De trop", agent=agent)

    texte = _texte(rendu.facture_pdf(premier.mentions))
    assert "Avoir n° AV-2026-000001" in texte and "facture n° F-2026-000001" in texte
    assert Audit.objects.filter(action="paiements.avoir.emis").count() == 2
    facture.refresh_from_db()
    assert facture.montant_ht == 10_000  # la facture, elle, n'a pas bougé


def test_l_export_comptable_est_borne_et_complet(compte, emetteur):
    services.emettre_facture(_achat(compte).id, maintenant=LE)
    sortie = io.StringIO()
    call_command("exporter_factures", debut="2026-11-01", fin="2026-12-01", stdout=sortie)
    lignes = sortie.getvalue().splitlines()
    assert lignes[0].startswith("numero;type;emise_le")
    assert lignes[1].startswith("F-2026-000001;FACTURE;2026-11-06T10:00:00")
    assert "L’Œuvre des Bénévoles;00042424B;10000;18;1800;11800" in lignes[1]

    vide = io.StringIO()
    call_command("exporter_factures", debut="2026-12-01", fin="2027-01-01", stdout=vide)
    assert len(vide.getvalue().splitlines()) == 1
