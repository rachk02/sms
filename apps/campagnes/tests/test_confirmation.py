"""Aperçu, envoi de test et confirmation d'une campagne (T-61).

Sous le rôle du worker ; préfixes fictifs, jamais la table réelle.
"""

import io

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.campagnes import services
from apps.campagnes.models import Campagne, Destinataire
from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts import liste_noire, listes
from apps.contacts.models import Contact
from apps.contacts.services import creer_contact
from apps.core.models import Audit, EvenementSortant
from apps.credits import services as credits
from apps.credits.models import Grille, Reservation
from apps.expediteurs import services as expediteurs
from apps.expediteurs.controle import Action
from apps.journal.models import Message
from apps.telecom.models import Prefixe

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("prix"),
]

PREFIXES = {"70": "ORANGE", "60": "MOOV", "58": "TELECEL"}


@pytest.fixture
def prix():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000"):
        yield


@pytest.fixture
def client(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        Prefixe(pays="BF", prefixe=p, operateur=o) for p, o in PREFIXES.items()
    )
    client = creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    credits.acheter(client.id, 100_000, reference="OM-1")
    yield client
    Prefixe.objects.filter(pays="BF").delete()


@pytest.fixture
def auteur(client):
    return creer_utilisateur(courriel="u@fasoriz.bf", mot_de_passe="x", nom="U", client=client)


@pytest.fixture
def fasoriz(client, auteur):
    """Approuvé chez Orange seulement."""
    agent = creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x", nom="Agent")
    expediteur = expediteurs.declarer(
        client=client,
        libelle="FASORIZ",
        mandat=io.BytesIO(b"%PDF-1.7 mandat"),
        nom_mandat="mandat.pdf",
        auteur=auteur,
    )
    expediteurs.deposer_dossier(expediteur.id, "ORANGE", agent=agent, reference="OBF-1")
    expediteurs.decider(
        expediteur.id, "ORANGE", agent=agent, approuve=True, decide_le=timezone.now(), reference="1"
    )
    return expediteur


@pytest.fixture
def cible(client):
    """Deux listes qui se recouvrent : 4 Orange, 3 Moov, 2 Telecel distincts."""
    numeros = {
        "70000001": "Awa",
        "70000002": "Ali",
        "70000003": "",
        "70000004": "Moussa",
        "60000001": "Issa",
        "60000002": "Fati",
        "60000003": "Adama",
        "58000001": "Zongo",
        "58000002": "Kaboré",
    }
    contacts = {
        numero: creer_contact(client=client, numero=numero, origine="SAISIE", prenom=prenom)
        for numero, prenom in numeros.items()
    }
    premiere = listes.creer(client=client, nom="Clients")
    seconde = listes.creer(client=client, nom="Salon")
    listes.ajouter(premiere, [c.id for n, c in contacts.items() if n < "70000003"])
    listes.ajouter(seconde, [c.id for c in contacts.values()])
    return [premiere.id, seconde.id]


def _campagne(client, auteur, cible, expediteur=None, politique="REPLI", texte=None):
    return services.creer(
        client=client,
        auteur=auteur,
        nom="Promo récolte",
        texte=texte or "Bonjour {prenom|cher client}, le riz est arrivé.",
        listes=cible,
        expediteur_id=expediteur.id if expediteur else None,
        politique=politique,
    )


def test_l_apercu_compte_chaque_numero_une_fois_hors_liste_noire(client, auteur, cible):
    liste_noire.bloquer(client=client, numero="70000004", origine="STOP_SMS")
    Contact.objects.filter(numero_normalise="+22658000002").update(actif=False)
    vu = services.apercu(_campagne(client, auteur, cible))
    assert vu.destinataires == 7
    assert vu.par_reseau == {"ORANGE": 3, "MOOV": 3, "TELECEL": 1}
    assert vu.par_expediteur == {(Action.ENVOYER, "36000"): 7}
    assert (vu.estimation.segments, vu.cout) == (1, 7 * 20)
    assert vu.disponible == 100_000 and vu.suffisant


@pytest.mark.parametrize(
    "politique, destinataires, par_expediteur",
    [
        ("REPLI", 9, {(Action.ENVOYER, "FASORIZ"): 4, (Action.REPLI, "36000"): 5}),
        ("REFUSER", 4, {(Action.ENVOYER, "FASORIZ"): 4, (Action.REFUSER, ""): 5}),
        ("ATTENDRE", 9, {(Action.ENVOYER, "FASORIZ"): 4, (Action.ATTENDRE, "FASORIZ"): 5}),
    ],
)
def test_l_apercu_applique_la_politique_d_expediteur_reseau_par_reseau(
    client, auteur, cible, fasoriz, politique, destinataires, par_expediteur
):
    vu = services.apercu(_campagne(client, auteur, cible, fasoriz, politique))
    assert (vu.destinataires, vu.par_expediteur) == (destinataires, par_expediteur)
    assert vu.refuses == (5 if politique == "REFUSER" else 0)
    assert vu.cout == destinataires * 20


def test_la_confirmation_fige_les_destinataires_et_reserve_le_cout(client, auteur, cible, fasoriz):
    campagne = _campagne(client, auteur, cible, fasoriz)
    vu = services.apercu(campagne)
    confirmee = services.confirmer(
        campagne, auteur=auteur, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )
    assert confirmee.etat == Campagne.Etat.EN_ATTENTE
    assert (confirmee.destinataires_prevus, confirmee.cout_estime) == (9, 180)
    figes = Destinataire.objects.filter(campagne=campagne)
    assert dict(figes.values_list("numero_normalise", "expediteur"))["+22660000001"] == "36000"
    assert figes.filter(action="ENVOYER", operateur="ORANGE").count() == 4
    reservation = Reservation.objects.get(campagne_id=campagne.id)
    assert reservation.montant_initial == 180
    assert credits.solde(client.id).disponible == 100_000 - 180
    assert Audit.objects.filter(action="campagnes.confirmation").exists()
    with pytest.raises(services.CampagneRefusee, match="déjà confirmée"):
        services.confirmer(campagne, auteur=auteur, destinataires_lus=9, cout_lu=180)


def test_des_chiffres_changes_depuis_l_apercu_refusent_la_confirmation(client, auteur, cible):
    campagne = _campagne(client, auteur, cible)
    vu = services.apercu(campagne)
    liste_noire.bloquer(client=client, numero="60000001", origine="STOP_SMS")  # entretemps
    with pytest.raises(services.ConfirmationPerimee, match="8 destinataires pour 160 FCFA"):
        services.confirmer(
            campagne, auteur=auteur, destinataires_lus=vu.destinataires, cout_lu=vu.cout
        )
    assert not Destinataire.objects.exists() and not Reservation.objects.exists()
    assert Campagne.objects.get(id=campagne.id).etat == Campagne.Etat.BROUILLON


def test_un_solde_insuffisant_refuse_la_confirmation_sans_rien_figer(client, auteur, cible):
    pauvre = creer_client(raison_sociale="Pauvre SA", forme=Client.Forme.SOCIETE)
    credits.acheter(pauvre.id, 50, reference="OM-2")
    contact = creer_contact(client=pauvre, numero="70000001", origine="SAISIE")
    liste = listes.creer(client=pauvre, nom="Tous")
    listes.ajouter(liste, [contact.id] * 1)
    for n in range(2, 5):
        listes.ajouter(
            liste, [creer_contact(client=pauvre, numero=f"7000000{n}", origine="SAISIE").id]
        )
    campagne = _campagne(pauvre, None, [liste.id])
    vu = services.apercu(campagne)
    assert (vu.cout, vu.suffisant) == (80, False)
    with pytest.raises(services.CampagneRefusee, match="Solde insuffisant : 50 FCFA disponibles"):
        services.confirmer(campagne, auteur=None, destinataires_lus=4, cout_lu=80)
    assert not Destinataire.objects.exists()


def test_l_envoi_de_test_est_facture_a_part_et_passe_par_la_file_prioritaire(
    client, auteur, cible, fasoriz
):
    campagne = _campagne(
        client, auteur, cible, fasoriz, texte="Bonjour {prenom|cher client}, voici l'offre."
    )
    message = services.envoyer_test(campagne, numero="60 99 88 77", auteur=auteur)
    assert (message.operateur, message.expediteur_libelle) == ("MOOV", "36000")
    # Le cas le plus long : le contact sans prénom, servi par la valeur de repli.
    assert message.campagne_id is None and message.texte == "Bonjour cher client, voici l'offre."
    reservation = Reservation.objects.get(message_id=message.id)
    assert reservation.campagne_id is None and reservation.montant_initial == 20
    file = EvenementSortant.objects.get(cle_idempotence=f"envoi:{message.id}").nom
    assert file.endswith("envoi.prioritaire.moov")
    assert Message.objects.filter(campagne_id=campagne.id).count() == 0
    with pytest.raises(services.CampagneRefusee, match="Numéro de test invalide"):
        services.envoyer_test(campagne, numero="123", auteur=auteur)


def test_une_cible_d_un_autre_compte_est_refusee(client, auteur, cible):
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    with pytest.raises(services.CampagneRefusee, match="Liste introuvable"):
        _campagne(autre, None, cible)
    with pytest.raises(services.CampagneRefusee, match="au moins une liste"):
        _campagne(client, auteur, [])


def _grille_publique(auteur, prix: dict):
    """Publie une version de la grille publique, créée au besoin, tout de suite,
    par un super-administrateur d'EL-IMMEKA."""
    from apps.comptes.models import Utilisateur

    auteur = Utilisateur.objects.filter(is_superuser=True).first() or creer_utilisateur(
        courriel="admin@el-immeka.test", mot_de_passe="x-1234-abcd", nom="A", is_superuser=True
    )
    grille = Grille.objects.filter(client__isnull=True).first() or credits.creer_grille(
        "Grille publique", auteur=auteur
    )
    version = credits.creer_version(grille)
    for operateur, montant in prix.items():
        credits.fixer_palier(version, pays="BF", operateur=operateur, prix_unitaire=montant)
    return credits.publier_version(version, auteur=auteur)


def test_l_envoi_de_test_prend_le_prix_en_vigueur_a_sa_date(client, auteur, cible):
    """T-81 : la grille change entre deux envois ; chacun garde son prix."""
    campagne = _campagne(client, auteur, cible)
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=None):
        _grille_publique(auteur, {"ORANGE": 20, "MOOV": 19, "TELECEL": 18})
        premier = services.envoyer_test(campagne, numero="60 99 88 77", auteur=auteur)
        _grille_publique(auteur, {"ORANGE": 20, "MOOV": 23, "TELECEL": 18})
        second = services.envoyer_test(campagne, numero="60 99 88 77", auteur=auteur)

    premier.refresh_from_db()
    assert (premier.cout_unitaire, second.cout_unitaire) == (19, 23)
    reservations = dict(
        Reservation.objects.filter(message_id__in=[premier.id, second.id]).values_list(
            "message_id", "montant_initial"
        )
    )
    assert reservations == {premier.id: 19, second.id: 23}


def test_sans_grille_publiee_l_apercu_est_refuse(client, auteur, cible):
    campagne = _campagne(client, auteur, cible)
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=None):
        with pytest.raises(services.CampagneRefusee, match="Aucun tarif en vigueur"):
            services.apercu(campagne)
        with pytest.raises(services.CampagneRefusee, match="Aucun tarif en vigueur"):
            services.envoyer_test(campagne, numero="60 99 88 77", auteur=auteur)
