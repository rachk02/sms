"""Coût d'une campagne figé à sa confirmation, palier de volume compris (T-82).

Sous le rôle du worker, sans prix de secours : seule la grille publique publiée
fixe les prix. Le worker d'envoi est appelé message par message sur ce que la
boîte d'envoi a publié, comme le ferait la file.
"""

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.campagnes import diffusion, selectors, services
from apps.campagnes.models import Campagne
from apps.campagnes.tests.test_pause import _en_file, client, worker  # noqa: F401
from apps.comptes.services import creer_utilisateur
from apps.contacts import listes
from apps.contacts.services import creer_contact
from apps.credits import services as credits
from apps.credits.models import Grille
from apps.journal.models import Compteur, Message
from apps.journal.selectors import debut_du_mois, volume_mensuel

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("sans_secours"),
]

PALIER, PRIX_BAS, PRIX_HAUT = 5, 15, 20


@pytest.fixture
def sans_secours():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=None, SMS_NUMERO_COURT="36000"):
        yield


@pytest.fixture
def agent(client):  # noqa: F811
    return creer_utilisateur(courriel="admin@el-immeka.test", mot_de_passe="x-1234-abcd", nom="A")


def _publier(agent, paliers: dict, debut=None):
    """Publie une version de la grille publique : {volume_min: prix} pour Orange."""
    grille = Grille.objects.filter(client__isnull=True).first() or credits.creer_grille(
        "Grille publique", auteur=agent
    )
    version = credits.creer_version(grille, debut=debut)
    for volume, prix in paliers.items():
        credits.fixer_palier(
            version, pays="BF", operateur="ORANGE", prix_unitaire=prix, volume_min=volume
        )
    return credits.publier_version(version, auteur=agent)


def _confirmee(client, rang: int, nombre: int) -> Campagne:  # noqa: F811
    liste = listes.creer(client=client, nom=f"Liste {rang}")
    listes.ajouter(
        liste,
        [
            creer_contact(
                client=client, numero=f"70{rang}{n:05d}", origine="SAISIE", prenom=f"P{n}"
            ).id
            for n in range(nombre)
        ],
    )
    campagne = services.creer(
        client=client,
        auteur=None,
        nom=f"Promo {rang}",
        texte="Bonjour {prenom|cher client}, promotion.",
        listes=[liste.id],
    )
    vu = services.apercu(campagne)
    return services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )


def _diffuser(worker, campagne):  # noqa: F811
    diffusion.publier()
    for donnees in _en_file(campagne):
        worker._soumettre(donnees)
    diffusion.publier()  # clôture
    campagne.refresh_from_db()


def _couts(campagne) -> set:
    return set(
        Message.objects.filter(campagne_id=campagne.id).values_list("cout_unitaire", flat=True)
    )


def test_le_palier_atteint_au_lancement_vaut_pour_toute_la_campagne(client, worker, agent):  # noqa: F811
    _publier(agent, {0: PRIX_HAUT, PALIER: PRIX_BAS})

    premiere = _confirmee(client, 1, PALIER + 3)
    assert premiere.tarification["volume_mensuel"] == 0
    orange = premiere.tarification["reseaux"]["ORANGE"]
    assert (orange["prix"], orange["palier"], orange["grille"]) == (PRIX_HAUT, 0, "PUBLIQUE")
    assert premiere.cout_estime == (PALIER + 3) * PRIX_HAUT

    # Le palier est franchi au cinquième message : les suivants gardent le prix figé.
    _diffuser(worker, premiere)
    assert _couts(premiere) == {PRIX_HAUT}
    assert premiere.cout_reel == (PALIER + 3) * PRIX_HAUT
    assert volume_mensuel(client.id) == PALIER + 3

    seconde = _confirmee(client, 2, 2)
    assert seconde.tarification["volume_mensuel"] == PALIER + 3
    orange = seconde.tarification["reseaux"]["ORANGE"]
    assert (orange["prix"], orange["palier"]) == (PRIX_BAS, PALIER)
    _diffuser(worker, seconde)
    assert _couts(seconde) == {PRIX_BAS}
    assert seconde.cout_reel == 2 * PRIX_BAS
    assert credits.solde(client.id) == credits.solde_recalcule(client.id)


def test_une_grille_changee_apres_la_confirmation_ne_touche_pas_la_campagne(client, worker, agent):  # noqa: F811
    _publier(agent, {0: PRIX_HAUT})
    campagne = _confirmee(client, 1, 4)
    effet = timezone.now() + timedelta(seconds=1)
    _publier(agent, {0: 30}, debut=effet)
    nouveau = services.tarifer(client, ["ORANGE"], effet + timedelta(seconds=1))
    assert nouveau["reseaux"]["ORANGE"]["prix"] == 30

    _diffuser(worker, campagne)

    assert _couts(campagne) == {PRIX_HAUT}
    assert campagne.cout_reel == 4 * PRIX_HAUT


def test_le_rapport_de_campagne_explique_le_tarif(client, worker, agent):  # noqa: F811
    _publier(agent, {0: PRIX_HAUT, PALIER: PRIX_BAS})
    _diffuser(worker, _confirmee(client, 1, PALIER))
    campagne = _confirmee(client, 2, 1)

    lignes = selectors.explication_tarif(campagne)

    assert f"volume de {PALIER} SMS soumis depuis le début du mois" in lignes[0]
    assert (
        lignes[1]
        == f"ORANGE : {PRIX_BAS} FCFA par SMS, palier à partir de {PALIER} SMS dans le mois."
    )
    assert "un palier franchi ou une grille changée pendant sa diffusion" in lignes[2]


def test_le_volume_du_mois_ignore_le_mois_precedent(client):  # noqa: F811
    debut = debut_du_mois()
    for heure, emis in (
        (debut - timedelta(hours=1), 50),
        (debut, 7),
        (debut + timedelta(days=2), 3),
    ):
        Compteur.objects.create(
            client_id=client.id,
            campagne_id=client.id,
            operateur="ORANGE",
            expediteur_libelle="36000",
            heure=heure,
            emis=emis,
        )
    assert volume_mensuel(client.id) == 10


def test_sans_grille_publiee_la_confirmation_est_refusee(client):  # noqa: F811
    liste = listes.creer(client=client, nom="Liste")
    listes.ajouter(liste, [creer_contact(client=client, numero="70100000", origine="SAISIE").id])
    campagne = services.creer(
        client=client, auteur=None, nom="Promo", texte="Bonjour.", listes=[liste.id]
    )
    with pytest.raises(services.CampagneRefusee, match="Aucun tarif en vigueur"):
        services.apercu(campagne)
