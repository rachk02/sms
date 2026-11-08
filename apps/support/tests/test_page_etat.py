"""Page publique d'état du service (T-103).

La page continue d'afficher le dernier état connu quand la plateforme ne répond
plus. Le passage en dégradé est automatique, le passage en panne demande une
action humaine.
"""

import json
import shutil
import subprocess
import uuid
from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.comptes.services import creer_utilisateur
from apps.core.models import Audit
from apps.support import page_etat, services
from apps.support.models import Incident
from apps.telecom.models import Connecteur

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

Composant = Incident.Composant
E = Incident.Etat

HARNAIS = """
require(process.argv[1]);
let entree = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (morceau) => (entree += morceau));
process.stdin.on("end", () => {
  const { etat, instants } = JSON.parse(entree);
  const resultats = instants.map((instant) => globalThis.PageEtat.modele(etat, instant));
  process.stdout.write(JSON.stringify(resultats));
});
"""


def _navigateur(etat: dict, instants: list) -> list:
    node = shutil.which("node")
    assert node, "node est requis pour vérifier la page d'état"
    sortie = subprocess.run(
        [node, "-e", HARNAIS, str(page_etat.SCRIPT)],
        input=json.dumps({"etat": etat, "instants": instants}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(sortie.stdout)


def _connecteur(operateur, etat):
    return Connecteur.objects.create(
        nom=f"{operateur.lower()}-{uuid.uuid4().hex[:6]}",
        operateur=operateur,
        type=Connecteur.Type.SIMULE,
        etat_liaison=etat,
    )


@pytest.fixture
def agent(en_tant_que_worker):
    return creer_utilisateur(
        courriel="exploitation@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Exploitation",
        is_staff=True,
    )


@pytest.fixture
def liaisons(en_tant_que_worker, monkeypatch):
    """Orange tombé, Moov et Telecel sains ; tous les workers battent."""
    _connecteur("ORANGE", Connecteur.EtatLiaison.DECONNECTE)
    _connecteur("MOOV", Connecteur.EtatLiaison.CONNECTE)
    _connecteur("TELECEL", Connecteur.EtatLiaison.CONNECTE)
    monkeypatch.setattr(
        "apps.core.battements.lire",
        lambda: [{"nom": nom, "exemplaire": "x"} for nom in ("accuses", "paiements")],
    )


def _par_code(etat):
    return {c["code"]: c for c in etat["composants"]}


def test_le_degrade_est_automatique_la_panne_ne_l_est_jamais(liaisons, monkeypatch):
    composants = _par_code(page_etat.construire())
    assert composants["ORANGE"]["etat"] == E.DEGRADE
    assert composants["ORANGE"]["source"] == "supervision"
    assert composants["MOOV"]["etat"] == page_etat.OPERATIONNEL
    assert all(c["etat"] != E.PANNE for c in composants.values())

    # Un worker sans battement dégrade son composant, jamais plus.
    monkeypatch.setattr("apps.core.battements.lire", lambda: [])
    composants = _par_code(page_etat.construire())
    assert composants["ACCUSES"]["etat"] == composants["RECHARGEMENT"]["etat"] == E.DEGRADE


def test_la_panne_se_declare_et_se_retablit(liaisons, agent):
    incident = services.declarer_incident(
        titre="Orange ne livre plus",
        composants=[Composant.ORANGE],
        etat=E.PANNE,
        texte="Aucun envoi vers Orange depuis 10 h ; Moov et Telecel ne sont pas touchés.",
        agent=agent,
    )
    etat = page_etat.construire()
    orange = _par_code(etat)["ORANGE"]
    assert (orange["etat"], orange["source"]) == (E.PANNE, "incident")
    assert [i["titre"] for i in etat["en_cours"]] == ["Orange ne livre plus"]

    services.publier_incident(
        incident,
        texte="Rétabli à 11 h, les messages en file sont repartis.",
        etat=E.RETABLI,
        agent=agent,
    )
    etat = page_etat.construire()
    # Rétabli, Orange revient à ce que dit la supervision.
    assert _par_code(etat)["ORANGE"]["etat"] == E.DEGRADE
    assert etat["en_cours"] == [] and len(etat["historique"]) == 1
    assert len(etat["historique"][0]["publications"]) == 2
    assert Audit.objects.filter(action__startswith="support.incident.").count() == 2


def test_une_maintenance_se_declare_a_l_avance_une_panne_non(liaisons, agent):
    demain = timezone.now() + timedelta(days=1)
    with pytest.raises(services.IncidentRefuse, match="maintenance"):
        services.declarer_incident(
            titre="Panne",
            composants=[Composant.API],
            etat=E.PANNE,
            texte="x",
            agent=agent,
            debut_le=demain,
        )
    services.declarer_incident(
        titre="Mise à jour",
        composants=[Composant.API],
        etat=E.MAINTENANCE,
        texte="Interruption de dix minutes.",
        agent=agent,
        debut_le=demain,
    )
    etat = page_etat.construire()
    assert [i["titre"] for i in etat["prevues"]] == ["Mise à jour"]
    assert _par_code(etat)["API"]["etat"] == page_etat.OPERATIONNEL


def test_la_page_affiche_le_dernier_etat_connu_quand_la_plateforme_ne_repond_plus(
    liaisons, tmp_path
):
    depot = page_etat.DepotDossier(tmp_path)
    publie_le = timezone.now()
    page_etat.publier(publie_le, cible=depot)

    # La page est autonome : son script est inclus, et elle ne lit que le
    # fichier d'état posé à côté d'elle, jamais la plateforme.
    page = depot.lire("index.html").decode()
    assert "/* SCRIPT */" not in page and "PageEtat" in page
    assert 'fetch("etat.json"' in page
    assert "http://" not in page and "https://" not in page

    # On coupe l'application : plus aucune publication. Le fichier reste, et la
    # page affiche le dernier état connu en disant qu'il est périmé.
    etat = json.loads(depot.lire("etat.json"))
    ms = int(publie_le.timestamp() * 1000)
    frais, perime = _navigateur(etat, [ms + 30_000, ms + 10 * 60_000])
    assert frais["perime"] is False
    assert perime["perime"] is True and perime["retard_minutes"] == 10
    assert {c["code"]: c["etat"] for c in perime["composants"]} == {
        c["code"]: c["etat"] for c in etat["composants"]
    }
    assert {c["code"]: c["etat"] for c in perime["composants"]}["ORANGE"] == E.DEGRADE


def test_le_depot_en_seau_garde_le_dernier_etat(en_tant_que_worker):
    config = settings.STOCKAGE_OBJET
    depot = page_etat.DepotSeau(
        config["point_acces"], config["identifiant"], config["secret"], "page-etat-essai"
    )
    depot.deposer("etat.json", b'{"version": 1}', "application/json")
    assert depot.lire("etat.json") == b'{"version": 1}'
    assert depot.lire("absent.json") is None


def test_l_exploitation_declare_et_publie_au_back_office(client, en_tant_que_backoffice):
    from django.contrib.auth.models import Group

    from apps.backoffice import roles

    exploitation = creer_utilisateur(
        courriel="exploitation@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Exploitation",
        is_staff=True,
    )
    exploitation.groups.add(Group.objects.get(name=roles.EXPLOITATION))
    support = creer_utilisateur(
        courriel="support@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Support",
        is_staff=True,
    )
    support.groups.add(Group.objects.get(name=roles.SUPPORT))

    client.force_login(support)
    assert client.get("/admin/support/incident/declarer/").status_code == 403
    client.force_login(exploitation)
    reponse = client.post(
        "/admin/support/incident/declarer/",
        {
            "titre": "Moov dégradé",
            "composants": ["MOOV"],
            "etat": "DEGRADE",
            "texte": "Livraisons lentes vers Moov.",
        },
    )
    assert reponse.status_code == 302
    incident = Incident.objects.get()
    reponse = client.post(
        f"/admin/support/incident/{incident.id}/publier/",
        {"etat": "RETABLI", "texte": "Rétabli, rien de perdu."},
    )
    assert reponse.status_code == 302
    incident.refresh_from_db()
    assert incident.etat == E.RETABLI and incident.fin_le is not None
    assert client.get("/admin/support/incident/").status_code == 200
