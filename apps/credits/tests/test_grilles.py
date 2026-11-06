"""Grilles tarifaires datées (T-81).

Les services s'exercent sous le rôle du worker, qui contourne le cloisonnement
comme le back-office qui publiera les grilles. Le cloisonnement se vérifie
sous le rôle applicatif, données posées par la connexion du worker.
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError, connections, transaction
from django.test import override_settings
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.cloisonnement import poser_client
from apps.core.models import Audit
from apps.credits import services, tarifs
from apps.credits.models import Grille, PalierGrille, VersionGrille
from apps.telecom.models import Prefixe

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

RESEAUX = {"70": "ORANGE", "60": "MOOV", "58": "TELECEL"}


@pytest.fixture
def sans_secours():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=None):
        yield


@pytest.fixture
def reseaux(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        [Prefixe(pays="BF", prefixe=p, operateur=o) for p, o in RESEAUX.items()],
        ignore_conflicts=True,
    )
    yield
    Prefixe.objects.filter(pays="BF").delete()


@pytest.fixture
def agent(reseaux):
    return creer_utilisateur(courriel="admin@el-immeka.test", mot_de_passe="x-1234-abcd", nom="A")


@pytest.fixture
def client(reseaux):
    return creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)


@pytest.fixture
def publique(agent):
    return services.creer_grille("Grille publique", auteur=agent)


def _version(grille, prix: dict, debut=None, paliers=()):
    version = services.creer_version(grille, debut=debut)
    for operateur, montant in prix.items():
        services.fixer_palier(version, pays="BF", operateur=operateur, prix_unitaire=montant)
    for operateur, volume, montant in paliers:
        services.fixer_palier(
            version, pays="BF", operateur=operateur, prix_unitaire=montant, volume_min=volume
        )
    return version


PRIX = {"ORANGE": 20, "MOOV": 19, "TELECEL": 18}


# Versions et chevauchement


def test_deux_versions_publiees_qui_se_chevauchent_sont_refusees_par_la_base(publique, agent):
    maintenant = timezone.now()
    services.publier_version(_version(publique, PRIX), auteur=agent, maintenant=maintenant)
    seconde = _version(publique, PRIX, debut=maintenant + timedelta(days=1))

    # En contournant le service, qui aurait clos la première : la base refuse.
    with pytest.raises(IntegrityError), transaction.atomic():
        VersionGrille.objects.filter(id=seconde.id).update(publiee=True, publiee_le=maintenant)


def test_un_brouillon_peut_recouvrir_une_version_publiee(publique, agent):
    services.publier_version(_version(publique, PRIX), auteur=agent)
    brouillon = _version(publique, {"ORANGE": 25}, debut=timezone.now() + timedelta(days=1))
    assert not brouillon.publiee


def test_publier_clot_la_version_en_vigueur_a_la_date_d_effet(publique, agent):
    maintenant = timezone.now()
    premiere = services.publier_version(
        _version(publique, PRIX), auteur=agent, maintenant=maintenant
    )
    effet = maintenant + timedelta(days=7)
    seconde = services.publier_version(
        _version(publique, {**PRIX, "ORANGE": 22}, debut=effet), auteur=agent, maintenant=maintenant
    )

    premiere.refresh_from_db()
    assert (premiere.debut, premiere.fin) == (maintenant, effet)
    assert (seconde.debut, seconde.fin) == (effet, None)


def test_une_version_ne_prend_pas_effet_dans_le_passe(publique, agent):
    version = _version(publique, PRIX, debut=timezone.now() - timedelta(minutes=1))
    with pytest.raises(services.TarifInvalide, match="dans le passé"):
        services.publier_version(version, auteur=agent)


def test_une_version_ne_s_intercale_pas_avant_une_version_deja_programmee(publique, agent):
    maintenant = timezone.now()
    services.publier_version(
        _version(publique, PRIX, debut=maintenant + timedelta(days=10)),
        auteur=agent,
        maintenant=maintenant,
    )
    with pytest.raises(services.TarifInvalide, match="prend déjà effet"):
        services.publier_version(
            _version(publique, PRIX, debut=maintenant + timedelta(days=3)),
            auteur=agent,
            maintenant=maintenant,
        )


def test_une_version_publiee_ne_change_plus(publique, agent):
    version = services.publier_version(_version(publique, PRIX), auteur=agent)
    with pytest.raises(services.TarifInvalide, match="ne change plus"):
        services.fixer_palier(version, pays="BF", operateur="ORANGE", prix_unitaire=1)
    with pytest.raises(services.TarifInvalide, match="déjà publiée"):
        services.publier_version(version, auteur=agent)


def test_la_grille_publique_couvre_chaque_reseau_du_pays(publique, agent):
    version = _version(publique, {"ORANGE": 20, "MOOV": 19})
    with pytest.raises(services.TarifInvalide, match="TELECEL"):
        services.publier_version(version, auteur=agent)


def test_chaque_reseau_tarife_a_un_prix_des_le_premier_segment(publique, agent):
    version = _version(publique, {"ORANGE": 20, "MOOV": 19})
    services.fixer_palier(version, pays="BF", operateur="TELECEL", prix_unitaire=15, volume_min=1)
    with pytest.raises(services.TarifInvalide, match="premier segment"):
        services.publier_version(version, auteur=agent)


def test_les_prix_sont_des_francs_entiers_positifs(publique):
    version = services.creer_version(publique)
    for prix in (0, -1, 19.5, "20"):
        with pytest.raises(services.TarifInvalide):
            services.fixer_palier(version, pays="BF", operateur="ORANGE", prix_unitaire=prix)


def test_une_seule_grille_publique(publique, agent):
    with pytest.raises(IntegrityError), transaction.atomic():
        services.creer_grille("Autre grille publique", auteur=agent)


def test_la_publication_est_tracee_avec_ancien_et_nouveau_prix(publique, agent):
    maintenant = timezone.now()
    premiere = services.publier_version(
        _version(publique, PRIX), auteur=agent, maintenant=maintenant
    )
    seconde = services.publier_version(
        _version(publique, {**PRIX, "ORANGE": 22}, debut=maintenant + timedelta(days=1)),
        auteur=agent,
        maintenant=maintenant,
    )

    trace = Audit.objects.get(action="credits.grille.publication", objet_id=str(seconde.id))
    assert trace.auteur_id == agent.id
    assert trace.valeur_avant["version"] == str(premiere.id)
    avant = {p["operateur"]: p["prix_unitaire"] for p in trace.valeur_avant["prix"]}
    apres = {p["operateur"]: p["prix_unitaire"] for p in trace.valeur_apres["prix"]}
    assert (avant["ORANGE"], apres["ORANGE"]) == (20, 22)


# Prix appliqué


def test_une_grille_sans_version_publiee_n_est_jamais_appliquee(
    publique, client, agent, sans_secours
):
    _version(publique, PRIX)  # brouillon complet, jamais publié
    with pytest.raises(tarifs.AucunTarif, match="Aucun tarif en vigueur pour le réseau ORANGE"):
        tarifs.prix_segment(client.id, "ORANGE")


def test_le_prix_de_secours_ne_remplace_jamais_un_brouillon(publique, client):
    _version(publique, {**PRIX, "ORANGE": 99})
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20):
        assert tarifs.prix_segment(client.id, "ORANGE") == 20


def test_le_prix_est_celui_en_vigueur_a_la_date(publique, client, agent, sans_secours):
    maintenant = timezone.now()
    services.publier_version(_version(publique, PRIX), auteur=agent, maintenant=maintenant)
    effet = maintenant + timedelta(hours=1)
    services.publier_version(
        _version(publique, {**PRIX, "ORANGE": 25}, debut=effet), auteur=agent, maintenant=maintenant
    )

    assert tarifs.prix_segment(client.id, "ORANGE", a=effet - timedelta(seconds=1)) == 20
    assert tarifs.prix_segment(client.id, "ORANGE", a=effet) == 25
    assert tarifs.prix_segment(client.id, "MOOV", a=effet) == 19
    with pytest.raises(tarifs.AucunTarif):
        tarifs.prix_segment(client.id, "ORANGE", a=maintenant - timedelta(seconds=1))


def test_le_palier_retenu_est_le_plus_haut_volume_atteint(publique, client, agent, sans_secours):
    version = _version(publique, PRIX, paliers=[("ORANGE", 10_000, 17), ("ORANGE", 100_000, 15)])
    services.publier_version(version, auteur=agent)

    assert tarifs.prix_segment(client.id, "ORANGE", volume=9_999) == 20
    assert tarifs.prix_segment(client.id, "ORANGE", volume=10_000) == 17
    assert tarifs.prix_segment(client.id, "ORANGE", volume=250_000) == 15
    assert tarifs.prix_segment(client.id, "MOOV", volume=250_000) == 19


def test_un_reseau_non_tarife_par_la_version_en_vigueur_est_refuse(publique, client, agent):
    services.publier_version(_version(publique, PRIX), auteur=agent)
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20):
        with pytest.raises(tarifs.AucunTarif, match="INCONNU"):
            tarifs.prix_segment(client.id, "INCONNU")


# Cloisonnement


def test_une_grille_negociee_ne_se_lit_que_par_son_client(transactional_db):
    """Sous le rôle applicatif : la grille publique pour tous, la grille de A
    pour A seul, versions et prix compris ; aucune écriture."""
    a = creer_client(raison_sociale="A SA", forme=Client.Forme.SOCIETE)
    b = creer_client(raison_sociale="B SA", forme=Client.Forme.SOCIETE)
    worker = Grille.objects.using("worker")
    publique = worker.create(nom="Publique")
    negociee = worker.create(nom="Négociée A", client_id=a.id)
    for grille, prix in ((publique, 20), (negociee, 12)):
        version = VersionGrille.objects.using("worker").create(grille=grille)
        PalierGrille.objects.using("worker").create(
            version=version, pays="BF", operateur="ORANGE", prix_unitaire=prix
        )

    def vus(client_id):
        with transaction.atomic():
            poser_client(client_id)
            return (
                set(Grille.objects.values_list("nom", flat=True)),
                VersionGrille.objects.count(),
                set(PalierGrille.objects.values_list("prix_unitaire", flat=True)),
            )

    assert vus(a.id) == ({"Publique", "Négociée A"}, 2, {20, 12})
    assert vus(b.id) == ({"Publique"}, 1, {20})

    with (
        pytest.raises(Exception, match="row-level security|permission denied"),
        transaction.atomic(),
    ):
        poser_client(a.id)
        Grille.objects.create(nom="Pirate", client_id=a.id)
    with connections["worker"].cursor() as curseur:
        curseur.execute("SELECT count(*) FROM credits_grille")
        assert curseur.fetchone()[0] == 2
