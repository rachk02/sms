"""Revue des dossiers par un agent, T-39, et passage de palier, T-40."""

import pytest
from django.utils.html import escape

from apps.backoffice.services import (
    CONTROLES_DE_REVUE,
    DecisionRefusee,
    PieceDeposee,
    approuver_dossier,
    enregistrer_client,
    rejeter_dossier,
)
from apps.comptes.models import Client, Dossier, Piece
from apps.comptes.services import PLAFONDS_PAR_PALIER, creer_utilisateur
from apps.conformite.models import Audit
from apps.core.models import EvenementSortant

pytestmark = pytest.mark.django_db(transaction=True)

PDF = b"%PDF-1.7\n" + b"0" * 256


@pytest.fixture
def agent(en_tant_que_backoffice):
    return creer_utilisateur(
        courriel="agent@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Agent",
        is_staff=True,
        is_superuser=True,
    )


def _dossier(agent, rang=1):
    pieces = {
        t: PieceDeposee(PDF, f"{t}.pdf") for t in ("RCCM", "IFU", "PIECE_GERANT", "DEMANDE_ECRITE")
    }
    return enregistrer_client(
        identite={
            "raison_sociale": f"Exemple {rang} SA",
            "forme": Client.Forme.SOCIETE,
            "rccm": f"BF-OUA-2026-B-{rang:04d}",
            "ifu": f"0001234{rang}A",
            "representant_nom": "Awa",
            "representant_courriel": f"awa{rang}@exemple.bf",
        },
        pieces=pieces,
        agent=agent,
    )


def test_un_rejet_sans_motif_de_la_liste_est_refuse(agent):
    dossier = _dossier(agent)
    for motif in ("", "Pas convaincu"):
        with pytest.raises(DecisionRefusee, match="liste des motifs"):
            rejeter_dossier(dossier.id, agent=agent, motif=motif, detail="précision")
    assert Dossier.objects.get(id=dossier.id).etat == Dossier.Etat.DEPOSE


def test_le_motif_de_rejet_part_au_client_mot_pour_mot_et_la_decision_est_auditee(agent):
    dossier = _dossier(agent)
    rejeter_dossier(
        dossier.id, agent=agent, motif=Dossier.MotifRejet.PIECE_EXPIREE, detail="IFU de 2023"
    )
    dossier.refresh_from_db()
    assert dossier.etat == Dossier.Etat.REJETE and dossier.decide_par_id == agent.id
    evenement = EvenementSortant.objects.get(nom="compte.dossier.rejete")
    assert evenement.donnees["libelle"] == "Pièce expirée"
    assert evenement.donnees["detail"] == "IFU de 2023"
    audit = Audit.objects.get(action="comptes.dossier.rejete")
    assert (audit.auteur_id, audit.motif) == (agent.id, "Pièce expirée") and audit.horodatage


def test_une_approbation_exige_les_cinq_controles(agent):
    dossier = _dossier(agent)
    with pytest.raises(DecisionRefusee, match="Contrôles non cochés"):
        approuver_dossier(dossier.id, agent=agent, controles=CONTROLES_DE_REVUE[:4])


def test_l_approbation_releve_palier_et_plafonds_sans_autre_intervention(agent):
    dossier = _dossier(agent)
    approuver_dossier(dossier.id, agent=agent, controles=CONTROLES_DE_REVUE)
    client = Client.objects.get(id=dossier.client_id)
    attendus = PLAFONDS_PAR_PALIER[Client.Palier.VERIFIE]
    assert client.palier == Client.Palier.VERIFIE
    assert (client.plafond_horaire, client.plafond_journalier) == (
        attendus["horaire"],
        attendus["journalier"],
    )
    assert set(Piece.objects.filter(dossier=dossier).values_list("etat", flat=True)) == {"ACCEPTEE"}
    audit = Audit.objects.get(action="comptes.dossier.approuve")
    assert (
        audit.valeur_avant["palier"] == "DECOUVERTE" and audit.valeur_apres["palier"] == "VERIFIE"
    )


def test_un_dossier_decide_ne_se_decide_pas_deux_fois(agent):
    dossier = _dossier(agent)
    approuver_dossier(dossier.id, agent=agent, controles=CONTROLES_DE_REVUE)
    with pytest.raises(DecisionRefusee, match="déjà"):
        rejeter_dossier(dossier.id, agent=agent, motif=Dossier.MotifRejet.DOUBLON)


def test_la_file_de_revue_se_prend_par_anciennete(client, agent):
    ancien, recent = _dossier(agent, 1), _dossier(agent, 2)
    client.force_login(agent)
    page = client.get("/admin/comptes/dossier/").content.decode()
    assert page.index(str(ancien.client)) < page.index(str(recent.client))


def test_la_fiche_de_revue_montre_les_pieces_par_liens_signes(client, agent):
    dossier = _dossier(agent)
    client.force_login(agent)
    page = client.get(f"/admin/comptes/dossier/{dossier.id}/change/").content.decode()
    assert page.count("X-Amz-Signature") == 4
    assert all(escape(controle) in page for controle in CONTROLES_DE_REVUE)


def test_la_decision_passe_par_l_ecran(client, agent):
    dossier = _dossier(agent)
    client.force_login(agent)
    reponse = client.post(
        f"/admin/comptes/dossier/{dossier.id}/change/",
        {"approuver": "1", "controles": list(CONTROLES_DE_REVUE)},
    )
    assert reponse.status_code == 302
    assert Dossier.objects.get(id=dossier.id).etat == Dossier.Etat.APPROUVE
