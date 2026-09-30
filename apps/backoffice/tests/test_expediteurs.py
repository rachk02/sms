"""Fiche d'un identifiant d'expéditeur au back-office (T-59)."""

import io

import pytest

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.models import Audit
from apps.expediteurs import services
from apps.expediteurs.models import ExpediteurReseau

pytestmark = pytest.mark.django_db(transaction=True)


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
def expediteur(agent):
    services.charger_reserves("libelle;motif\nBANQUE;Mot générique trompeur\n")
    compte = creer_client(raison_sociale="Banque du Faso", forme=Client.Forme.SOCIETE)
    return services.declarer(
        client=compte,
        libelle="BanqueFaso",
        mandat=io.BytesIO(b"%PDF-1.7 mandat"),
        nom_mandat="mandat.pdf",
        auteur=None,
    )


def _etat(expediteur, operateur):
    return ExpediteurReseau.objects.get(expediteur=expediteur, operateur=operateur).etat


def test_la_fiche_montre_la_revue_le_mandat_et_les_trois_reseaux(client, agent, expediteur):
    client.force_login(agent)
    page = client.get(f"/admin/expediteurs/expediteur/{expediteur.id}/change/").content.decode()
    assert "Nom réservé : BANQUE" in page and "X-Amz-Signature" in page
    assert all(f"{o.title()} : Non soumis" in page for o in ("Orange", "Moov", "Telecel"))


def test_de_la_revue_a_la_decision_par_l_ecran(client, agent, expediteur):
    client.force_login(agent)
    fiche = f"/admin/expediteurs/expediteur/{expediteur.id}/change/"
    refus = client.post(
        fiche, {"deposer": "1", "operateur": "ORANGE", "reference": "OBF-9"}, follow=True
    )
    assert "levez d&#x27;abord la revue manuelle" in refus.content.decode()
    client.post(fiche, {"lever_revue": "1", "motif": "Agrément bancaire vérifié"})
    client.post(fiche, {"deposer": "1", "operateur": "ORANGE", "reference": "OBF-9"})
    assert _etat(expediteur, "ORANGE") == "EN_INSTRUCTION"
    client.post(
        fiche,
        {
            "decider": "1",
            "operateur": "ORANGE",
            "decision": "refuse",
            "date": "2026-09-29",
            "reference": "OBF-9",
            "motif": "Mandat expiré",
        },
    )
    reseau = ExpediteurReseau.objects.get(expediteur=expediteur, operateur="ORANGE")
    assert (reseau.etat, reseau.motif_refus) == ("REFUSE", "Mandat expiré")
    assert reseau.decide_le.date().isoformat() == "2026-09-29"
    actions = set(Audit.objects.values_list("action", flat=True))
    assert {
        "expediteurs.revue.levee",
        "expediteurs.reseau.depot",
        "expediteurs.reseau.decision",
    } <= actions
