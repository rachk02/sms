"""Supervision des liaisons au back-office (T-96).

Une session tombée apparaît avec son heure de chute dès la première évaluation,
qui tourne toutes les quinze secondes. La profondeur des files est celle que
rend le courtier. Une file qui monte et une file qui stagne ne se confondent
pas.
"""

import uuid

import pytest
from django.contrib.auth.models import Group

from apps.backoffice import roles, selectors
from apps.comptes.services import creer_utilisateur
from apps.core import battements
from apps.telecom import sante
from apps.telecom.models import Connecteur, IncidentLiaison, Operateur

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.mark.parametrize(
    ("nom", "profondeur", "consommateurs", "sorties", "variation", "attendu"),
    [
        ("envoi.masse.orange", 0, 1, 0.0, 0.0, "VIDE"),
        ("envoi.masse.orange", 900, 2, 40.0, 12.0, "MONTE"),
        ("envoi.masse.orange", 900, 2, 40.0, -5.0, "S_ECOULE"),
        ("envoi.masse.orange", 900, 2, 0.0, 0.0, "STAGNE"),
        ("envoi.masse.orange", 900, 0, 0.0, 3.0, "STAGNE"),
        ("reessai.2", 40, 0, 0.0, 1.0, "PALIER"),
        ("test-1a2b.reessai.5", 40, 0, 0.0, 1.0, "PALIER"),
        ("rebut", 3, 0, 0.0, 0.0, "A_INSPECTER"),
        ("rebut", 0, 0, 0.0, 0.0, "VIDE"),
    ],
)
def test_une_file_qui_monte_n_est_pas_une_file_qui_stagne(
    nom, profondeur, consommateurs, sorties, variation, attendu
):
    code, explication = selectors.tendance(nom, profondeur, consommateurs, sorties, variation)
    assert code == attendu and explication


def test_la_profondeur_est_celle_du_courtier(monkeypatch):
    monkeypatch.setattr(
        selectors,
        "files_du_courtier",
        lambda: [
            {
                "name": "envoi.masse.moov",
                "messages": 1234,
                "consumers": 1,
                "messages_details": {"rate": 0.0, "avg_rate": 4.5},
                "message_stats": {
                    "publish_details": {"rate": 30.0, "avg_rate": 34.5},
                    "deliver_get_details": {"rate": 30.0, "avg_rate": 30.0},
                },
            },
            {"name": "import.contacts", "messages": 0, "consumers": 1},
        ],
    )
    files = {f.nom: f for f in selectors.files_supervisees()}
    assert files["envoi.masse.moov"].profondeur == 1234
    assert files["envoi.masse.moov"].tendance == "MONTE"
    assert (files["envoi.masse.moov"].entrees_s, files["envoi.masse.moov"].sorties_s) == (
        34.5,
        30.0,
    )
    assert files["import.contacts"].tendance == "VIDE"


def test_un_courtier_muet_se_dit(monkeypatch):
    monkeypatch.setattr(selectors, "files_du_courtier", lambda: None)
    assert selectors.files_supervisees() is None


def test_les_workers_attendus_suivent_les_classes():
    from workers.accuses import WorkerAccuses
    from workers.envoi import WorkerEnvoi
    from workers.import_contacts import WorkerImport
    from workers.ordonnanceur import Ordonnanceur
    from workers.paiements import WorkerPaiements
    from workers.webhook import WorkerWebhook

    classes = (
        Ordonnanceur,
        WorkerEnvoi,
        WorkerAccuses,
        WorkerImport,
        WorkerPaiements,
        WorkerWebhook,
    )
    assert {c.nom for c in classes} == set(battements.ATTENDUS)


def test_un_battement_vit_une_minute_et_s_efface_a_l_arret():
    ident = f"essai:{uuid.uuid4().hex[:6]}"
    battements.battre("envoi", ident, demarre_le=1_700_000_000, etat="ACTIF", en_cours=3)
    vivant = next(b for b in battements.lire() if b["exemplaire"] == ident)
    assert (vivant["nom"], vivant["en_cours"], vivant["etat"]) == ("envoi", 3, "ACTIF")
    ttl = battements.client_redis().ttl(f"{battements.PREFIXE}envoi:{ident}")
    assert 0 < ttl <= battements.DUREE_DE_VIE_S
    battements.oublier("envoi", ident)
    assert all(b["exemplaire"] != ident for b in battements.lire())


@pytest.fixture
def exploitation(en_tant_que_backoffice, monkeypatch):
    monkeypatch.setattr(selectors, "files_du_courtier", lambda: [])
    agent = creer_utilisateur(
        courriel="exploitation@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Exploitation",
        is_staff=True,
    )
    agent.groups.add(Group.objects.get(name=roles.EXPLOITATION))
    connecteur = Connecteur.objects.create(
        nom=f"orange-{uuid.uuid4().hex[:6]}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        parametres={"coupe": True},
    )
    yield agent, connecteur
    sante.client_redis().delete(sante.CLE.format(connecteur_id=connecteur.id))


def test_une_session_tombee_apparait_avec_son_heure_de_chute(client, exploitation):
    agent, connecteur = exploitation
    sante.surveiller()
    sante.surveiller()
    incident = IncidentLiaison.objects.get(connecteur=connecteur)
    assert incident.etat == Connecteur.EtatLiaison.DECONNECTE and incident.fin is None
    assert incident.motif == "session tombée"

    client.force_login(agent)
    page = client.get("/admin/telecom/incidentliaison/supervision/")
    assert page.status_code == 200
    fragment = client.get("/admin/telecom/incidentliaison/supervision/fragment/")
    contenu = fragment.content.decode()
    ligne = next(ligne for ligne in contenu.split("<tr>") if connecteur.nom in ligne)
    assert "Déconnecté" in ligne and "tombé le" in ligne and "session tombée" in ligne
    # L'heure affichée est celle de la chute, en heure de Ouagadougou.
    from django.utils import timezone

    assert timezone.localtime(incident.debut).strftime("%H:%M:%S") in ligne
    assert client.get("/admin/telecom/incidentliaison/").status_code == 200

    connecteur.parametres = {}
    connecteur.save()
    sante.surveiller()
    incident.refresh_from_db()
    assert incident.fin is not None
    assert not IncidentLiaison.objects.filter(connecteur=connecteur, fin__isnull=True).exists()


def test_une_route_degradee_puis_coupee_change_d_incident(exploitation):
    _, connecteur = exploitation
    connecteur.parametres = {}
    connecteur.save()
    maintenant = sante.timezone.now()
    for _ in range(30):
        sante.enregistrer_accuse(connecteur.id, False, 2.0, maintenant)
    sante.surveiller(maintenant)
    degrade = IncidentLiaison.objects.get(connecteur=connecteur)
    assert degrade.etat == Connecteur.EtatLiaison.DEGRADE
    assert degrade.mesures["accuses"] == 30 and degrade.mesures["taux"] == 0

    connecteur.parametres = {"coupe": True}
    connecteur.save()
    sante.surveiller(maintenant)
    degrade.refresh_from_db()
    assert degrade.fin is not None
    ouvert = IncidentLiaison.objects.get(connecteur=connecteur, fin__isnull=True)
    assert ouvert.etat == Connecteur.EtatLiaison.DECONNECTE


def test_un_worker_sans_battement_se_voit(exploitation, client):
    agent, _ = exploitation
    ident = f"essai:{uuid.uuid4().hex[:6]}"
    battements.battre("envoi", ident, demarre_le=1_700_000_000, etat="ACTIF", en_cours=0)
    try:
        vue = selectors.supervision()
        par_nom = {w.nom: w for w in vue.workers}
        assert [e["exemplaire"] for e in par_nom["envoi"].exemplaires] == [ident]
        client.force_login(agent)
        contenu = client.get("/admin/telecom/incidentliaison/supervision/fragment/")
        assert "Aucun exemplaire en vie." in contenu.content.decode()
    finally:
        battements.oublier("envoi", ident)
