"""Grilles négociées, promotions et règle de priorité (T-83).

Réseau par réseau : le plus bas de la grille négociée et des promotions qui
visent le client ; sans l'une ni l'autre, la grille publique. Sous le rôle du
worker, sans prix de secours.
"""

from datetime import timedelta

import pytest
from django.db import IntegrityError, transaction
from django.test import override_settings
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.models import Audit
from apps.credits import services, tarifs
from apps.credits.models import Grille
from apps.telecom.models import Prefixe

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("sans_secours"),
]

Nature = Grille.Nature
PUBLIC, NEGOCIE, PROMO_BASSE, PROMO_HAUTE = 20, 15, 12, 18
RESEAUX = {"70": "ORANGE", "60": "MOOV", "58": "TELECEL"}


@pytest.fixture
def sans_secours():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=None):
        yield


@pytest.fixture
def agent(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        [Prefixe(pays="BF", prefixe=p, operateur=o) for p, o in RESEAUX.items()],
        ignore_conflicts=True,
    )
    yield creer_utilisateur(
        courriel="admin@el-immeka.test", mot_de_passe="x-1234-abcd", nom="A", is_superuser=True
    )
    Prefixe.objects.filter(pays="BF").delete()


@pytest.fixture
def client(agent):
    return creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)


@pytest.fixture
def publique(agent):
    grille = services.creer_grille("Grille publique", auteur=agent)
    _publier(grille, agent, dict.fromkeys(RESEAUX.values(), PUBLIC))
    return grille


def _publier(grille, agent, prix: dict, *, debut=None, fin=None):
    version = services.creer_version(grille, debut=debut, fin=fin)
    for operateur, montant in prix.items():
        services.fixer_palier(version, pays="BF", operateur=operateur, prix_unitaire=montant)
    return services.publier_version(version, auteur=agent)


def _negociee(client, agent, prix):
    grille = services.creer_grille(
        f"Négociée {client.raison_sociale}",
        auteur=agent,
        nature=Nature.NEGOCIEE,
        client_id=client.id,
    )
    _publier(grille, agent, prix)
    return grille


def _promotion(agent, prix, *, client_id=None, jours=7):
    grille = services.creer_grille(
        "Promotion", auteur=agent, nature=Nature.PROMOTION, client_id=client_id
    )
    _publier(grille, agent, prix, fin=timezone.now() + timedelta(days=jours))
    return grille


@pytest.mark.parametrize(
    "negociee, promotion, attendu, nature",
    [
        (None, None, PUBLIC, Nature.PUBLIQUE),
        (None, PROMO_BASSE, PROMO_BASSE, Nature.PROMOTION),
        (None, PROMO_HAUTE, PROMO_HAUTE, Nature.PROMOTION),
        (NEGOCIE, None, NEGOCIE, Nature.NEGOCIEE),
        (NEGOCIE, PROMO_BASSE, PROMO_BASSE, Nature.PROMOTION),
        (NEGOCIE, PROMO_HAUTE, NEGOCIE, Nature.NEGOCIEE),
    ],
    ids=[
        "publique seule",
        "promotion seule, basse",
        "promotion seule, haute",
        "negociee seule",
        "negociee et promotion plus basse",
        "negociee et promotion plus haute",
    ],
)
def test_la_regle_de_priorite_sur_les_six_combinaisons(
    publique, client, agent, negociee, promotion, attendu, nature
):
    if negociee:
        _negociee(client, agent, {"ORANGE": negociee})
    if promotion:
        _promotion(agent, {"ORANGE": promotion})

    retenu = tarifs.tarif(client.id, "ORANGE")

    assert (retenu.prix, retenu.nature) == (attendu, nature)


def test_un_client_sans_grille_negociee_tombe_sur_la_grille_publique(publique, client, agent):
    autre = creer_client(raison_sociale="Kossi Transport", forme=Client.Forme.SOCIETE)
    _negociee(autre, agent, {"ORANGE": NEGOCIE})
    assert tarifs.prix_segment(client.id, "ORANGE") == PUBLIC
    assert tarifs.prix_segment(autre.id, "ORANGE") == NEGOCIE


def test_une_promotion_reservee_ne_vaut_que_pour_son_client(publique, client, agent):
    autre = creer_client(raison_sociale="Kossi Transport", forme=Client.Forme.SOCIETE)
    _promotion(agent, {"ORANGE": PROMO_BASSE}, client_id=autre.id)
    assert tarifs.prix_segment(client.id, "ORANGE") == PUBLIC
    assert tarifs.prix_segment(autre.id, "ORANGE") == PROMO_BASSE


def test_une_promotion_echue_ne_s_applique_plus(publique, client, agent):
    _promotion(agent, {"ORANGE": PROMO_BASSE}, jours=3)
    assert tarifs.prix_segment(client.id, "ORANGE") == PROMO_BASSE
    plus_tard = timezone.now() + timedelta(days=4)
    assert tarifs.prix_segment(client.id, "ORANGE", a=plus_tard) == PUBLIC


def test_une_grille_negociee_partielle_laisse_les_autres_reseaux_au_public(publique, client, agent):
    _negociee(client, agent, {"ORANGE": NEGOCIE})
    assert tarifs.prix_segment(client.id, "ORANGE") == NEGOCIE
    assert tarifs.prix_segment(client.id, "MOOV") == PUBLIC


def test_a_prix_egal_la_grille_negociee_l_emporte(publique, client, agent):
    _negociee(client, agent, {"ORANGE": NEGOCIE})
    _promotion(agent, {"ORANGE": NEGOCIE})
    assert tarifs.tarif(client.id, "ORANGE").nature == Nature.NEGOCIEE


def test_un_prix_negocie_plus_haut_que_le_public_reste_celui_du_client(publique, client, agent):
    _negociee(client, agent, {"ORANGE": PUBLIC + 5})
    assert tarifs.prix_segment(client.id, "ORANGE") == PUBLIC + 5


def test_sans_grille_publique_une_grille_negociee_suffit(client, agent):
    _negociee(client, agent, {"ORANGE": NEGOCIE})
    assert tarifs.prix_segment(client.id, "ORANGE") == NEGOCIE
    with pytest.raises(tarifs.AucunTarif):
        tarifs.prix_segment(client.id, "MOOV")


def test_une_promotion_porte_toujours_une_fin(agent):
    grille = services.creer_grille("Promotion", auteur=agent, nature=Nature.PROMOTION)
    version = services.creer_version(grille)
    services.fixer_palier(version, pays="BF", operateur="ORANGE", prix_unitaire=PROMO_BASSE)
    with pytest.raises(services.TarifInvalide, match="durée limitée"):
        services.publier_version(version, auteur=agent)


def test_natures_et_clients_sont_coherents(client, agent):
    with pytest.raises(services.TarifInvalide, match="appartient à un client"):
        services.creer_grille("Sans client", auteur=agent, nature=Nature.NEGOCIEE)
    with pytest.raises(services.TarifInvalide, match="aucun client"):
        services.creer_grille("Publique", auteur=agent, client_id=client.id)
    _negociee(client, agent, {"ORANGE": NEGOCIE})
    with pytest.raises(IntegrityError), transaction.atomic():
        services.creer_grille(
            "Seconde négociée", auteur=agent, nature=Nature.NEGOCIEE, client_id=client.id
        )
    with pytest.raises(IntegrityError), transaction.atomic():
        Grille.objects.create(nom="Incohérente", nature=Nature.PUBLIQUE, client_id=client.id)


def test_modifier_une_grille_negociee_trace_l_ancien_et_le_nouveau_prix(publique, client, agent):
    grille = _negociee(client, agent, {"ORANGE": NEGOCIE})
    version = _publier(
        grille, agent, {"ORANGE": NEGOCIE - 2}, debut=timezone.now() + timedelta(days=1)
    )

    trace = Audit.objects.get(action="credits.grille.publication", objet_id=str(version.id))
    assert trace.client_id == client.id and trace.auteur_id == agent.id
    assert [p["prix_unitaire"] for p in trace.valeur_avant["prix"]] == [NEGOCIE]
    assert [p["prix_unitaire"] for p in trace.valeur_apres["prix"]] == [NEGOCIE - 2]
    assert trace.valeur_apres["nature"] == Nature.NEGOCIEE
