"""Déclaration d'un identifiant (T-58) et états par réseau (T-59).

Sous le rôle du worker, qui contourne le cloisonnement comme le back-office ;
la garantie « le client ne modifie jamais un état réseau » est vérifiée sous le
rôle applicatif, client posé.
"""

import io
from datetime import timedelta

import pytest
from django.db import IntegrityError, ProgrammingError, transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.cloisonnement import poser_client
from apps.core.models import Audit
from apps.expediteurs import services
from apps.expediteurs.models import Expediteur, ExpediteurReseau, Reserve
from apps.fichiers.models import Fichier

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

MANDAT = b"%PDF-1.7 mandat signe"


@pytest.fixture
def client(en_tant_que_worker):
    return creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)


@pytest.fixture
def agent(en_tant_que_worker):
    return creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x", nom="Agent")


def _declarer(client, libelle="FASORIZ", auteur=None):
    return services.declarer(
        client=client,
        libelle=libelle,
        mandat=io.BytesIO(MANDAT),
        nom_mandat="mandat.pdf",
        auteur=auteur,
    )


@pytest.mark.parametrize(
    "libelle, message",
    [
        ("FASORIZPROMO", r"11 caractères au plus \(12 saisis\)"),
        ("FasoRizé", "Lettres sans accent et chiffres seulement"),
        ("Faso Riz", "Lettres sans accent et chiffres seulement"),
        ("70123456", "Au moins une lettre"),
        ("  ", "Saisissez l'identifiant"),
    ],
)
def test_un_libelle_hors_regles_est_refuse(client, libelle, message):
    with pytest.raises(services.ExpediteurRefuse, match=message):
        _declarer(client, libelle)
    assert not Expediteur.objects.exists()


def test_la_base_refuse_aussi_un_libelle_hors_alphabet(client):
    with pytest.raises(IntegrityError), transaction.atomic():
        Expediteur.objects.create(client=client, libelle="Faso Riz")


def test_une_declaration_cree_une_ligne_par_reseau_et_garde_le_mandat_chiffre(client):
    expediteur = _declarer(client)
    assert expediteur.etat == Expediteur.Etat.SOUMIS and not expediteur.revue_manuelle
    etats = dict(expediteur.reseaux.values_list("operateur", "etat"))
    assert etats == {o: "NON_SOUMIS" for o in ("ORANGE", "MOOV", "TELECEL")}
    piece = Fichier.objects.get(id=expediteur.justificatif_id)
    assert piece.chiffre and piece.famille == Fichier.Famille.PIECE
    assert Audit.objects.filter(action="expediteurs.declaration", objet_id=str(expediteur.id))
    with pytest.raises(services.ExpediteurRefuse, match="déjà déclaré"):
        _declarer(client, "fasoriz")


def test_un_mandat_qui_n_est_pas_une_piece_est_refuse(client):
    with pytest.raises(services.ExpediteurRefuse, match="Pièce de mandat"):
        services.declarer(
            client=client,
            libelle="FASORIZ",
            mandat=io.BytesIO(b"texte quelconque"),
            nom_mandat="mandat.pdf",
            auteur=None,
        )


def test_un_nom_reserve_part_en_revue_et_n_est_jamais_depose_d_office(client, agent):
    services.charger_reserves("libelle;motif\nOrange;Opérateur de réseau\n")
    expediteur = _declarer(client, "OrangePromo")
    assert expediteur.revue_manuelle and "ORANGE" in expediteur.motif_revue
    with pytest.raises(services.DecisionRefusee, match="levez d'abord la revue"):
        services.deposer_dossier(expediteur.id, "ORANGE", agent=agent, reference="OBF-1")
    with pytest.raises(services.DecisionRefusee, match="motif"):
        services.lever_revue(expediteur.id, agent=agent, motif=" ")
    services.lever_revue(expediteur.id, agent=agent, motif="Mandat d'Orange BF vérifié")
    entree = Audit.objects.get(action="expediteurs.revue.levee")
    assert entree.motif == "Mandat d'Orange BF vérifié" and entree.auteur_id == agent.id
    reseau = services.deposer_dossier(expediteur.id, "ORANGE", agent=agent, reference="OBF-1")
    assert reseau.etat == ExpediteurReseau.Etat.EN_INSTRUCTION


def test_un_meme_identifiant_porte_trois_etats_distincts(client, agent):
    expediteur = _declarer(client)
    hier = timezone.now() - timedelta(days=1)
    for operateur in ("ORANGE", "MOOV"):
        services.deposer_dossier(expediteur.id, operateur, agent=agent, reference=f"D-{operateur}")
    with pytest.raises(services.DecisionRefusee, match="motif du refus"):
        services.decider(
            expediteur.id, "MOOV", agent=agent, approuve=False, decide_le=hier, reference="D-MOOV"
        )
    services.decider(
        expediteur.id, "ORANGE", agent=agent, approuve=True, decide_le=hier, reference="D-ORANGE"
    )
    services.decider(
        expediteur.id,
        "MOOV",
        agent=agent,
        approuve=False,
        decide_le=hier,
        reference="D-MOOV",
        motif="Marque non justifiée",
    )
    with pytest.raises(services.DecisionRefusee, match="déposez-le d'abord"):
        services.decider(
            expediteur.id, "TELECEL", agent=agent, approuve=True, decide_le=hier, reference="x"
        )
    etats = dict(expediteur.reseaux.values_list("operateur", "etat"))
    assert etats == {"ORANGE": "APPROUVE", "MOOV": "REFUSE", "TELECEL": "NON_SOUMIS"}
    expediteur.refresh_from_db()
    assert expediteur.etat == Expediteur.Etat.ACTIF
    decisions = Audit.objects.filter(action="expediteurs.reseau.decision").order_by("horodatage")
    assert [d.valeur_apres["etat"] for d in decisions] == ["APPROUVE", "REFUSE"]
    assert decisions.last().motif == "Marque non justifiée"


def test_le_client_ne_peut_pas_modifier_un_etat_reseau():
    """Sous le rôle applicatif, client posé : la base elle-même refuse."""
    client = creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)
    with transaction.atomic():
        poser_client(client.id)
        expediteur = _declarer(client)
        modifiees = ExpediteurReseau.objects.filter(expediteur=expediteur).update(
            etat=ExpediteurReseau.Etat.APPROUVE
        )
        assert modifiees == 0
        assert set(expediteur.reseaux.values_list("etat", flat=True)) == {"NON_SOUMIS"}
    with pytest.raises(ProgrammingError, match="row-level security"), transaction.atomic():
        poser_client(client.id)
        autre = Expediteur.objects.create(client=client, libelle="AUTRE")
        ExpediteurReseau.objects.create(
            client=client, expediteur=autre, operateur="ORANGE", etat="APPROUVE"
        )
    with pytest.raises(ProgrammingError, match="row-level security"), transaction.atomic():
        poser_client(client.id)
        Reserve.objects.create(libelle="FASORIZ", motif="tentative")


def test_un_fichier_de_noms_reserves_mal_forme_est_refuse(en_tant_que_worker):
    with pytest.raises(services.FichierReservesInvalide, match="Ligne 3"):
        services.charger_reserves("libelle;motif\nARCEP;Régulateur\nBCEAO\n")
    assert services.charger_reserves("libelle;motif\nArcep;Régulateur\n") == 1
    assert Reserve.objects.get().libelle == "ARCEP"
