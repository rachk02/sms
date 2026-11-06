"""Deux campagnes confirmées en même temps sur un solde qui n'en couvre qu'une (T-79).

Deux transactions réelles, une par fil d'exécution et donc une connexion
PostgreSQL chacune, sous le rôle du worker. Rien n'est simulé : le verrou est
celui de la ligne `credits_compte`.
"""

import threading
import uuid

import pytest
from django.db import connection, transaction
from django.test import override_settings

from apps.campagnes import services
from apps.campagnes.models import Campagne, Destinataire
from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts import listes
from apps.contacts.services import creer_contact
from apps.credits import services as credits
from apps.credits.models import CompteCredit, Reservation
from apps.telecom.models import Prefixe

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("reglages"),
]

PRIX, DESTINATAIRES = 20, 3
COUT = PRIX * DESTINATAIRES
SOLDE = COUT + COUT // 2  # couvre une campagne, pas deux


@pytest.fixture
def reglages():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=PRIX, SMS_NUMERO_COURT="36000"):
        yield


@pytest.fixture
def client(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        [Prefixe(pays="BF", prefixe="70", operateur="ORANGE")], ignore_conflicts=True
    )
    client = creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    credits.acheter(client.id, SOLDE, reference="OM-1")
    yield client
    Prefixe.objects.filter(pays="BF").delete()


def _brouillon(client, rang: int) -> Campagne:
    liste = listes.creer(client=client, nom=f"Liste {rang}")
    listes.ajouter(
        liste,
        [
            creer_contact(
                client=client, numero=f"70{rang}{n:05d}", origine="SAISIE", prenom=f"P{n}"
            ).id
            for n in range(DESTINATAIRES)
        ],
    )
    return services.creer(
        client=client,
        auteur=None,
        nom=f"Promo {rang}",
        texte="Bonjour {prenom|cher client}, promotion.",
        listes=[liste.id],
    )


def _confirmer(campagne, resultats, depart=None):
    try:
        if depart:
            depart.wait(5)
        resultats[campagne.id] = services.confirmer(
            campagne, auteur=None, destinataires_lus=DESTINATAIRES, cout_lu=COUT
        )
    except services.CampagneRefusee as refus:
        resultats[campagne.id] = refus
    finally:
        connection.close()


def _soldes(client):
    return credits.solde(client.id), credits.solde_recalcule(client.id)


@pytest.mark.parametrize("essai", range(3))
def test_deux_campagnes_simultanees_une_seule_passe(client, essai):
    campagnes = [_brouillon(client, rang) for rang in (1, 2)]
    assert services.apercu(campagnes[0]).cout == COUT
    depart, resultats = threading.Barrier(2), {}

    fils = [threading.Thread(target=_confirmer, args=(c, resultats, depart)) for c in campagnes]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(30)

    passees = [r for r in resultats.values() if isinstance(r, Campagne)]
    refus = [r for r in resultats.values() if isinstance(r, services.CampagneRefusee)]
    assert len(passees) == 1 and len(refus) == 1
    assert str(refus[0]) == (
        f"Solde insuffisant : {SOLDE - COUT} FCFA disponibles, {COUT} nécessaires."
    )

    gagnante = passees[0]
    perdante = next(c for c in campagnes if c.id != gagnante.id)
    perdante.refresh_from_db()
    assert perdante.etat == Campagne.Etat.BROUILLON
    assert not Destinataire.objects.filter(campagne=perdante).exists()
    assert not Reservation.objects.filter(campagne_id=perdante.id).exists()
    assert Destinataire.objects.filter(campagne=gagnante).count() == DESTINATAIRES

    cache, recalcule = _soldes(client)
    assert cache == recalcule == credits.Solde(SOLDE, COUT)


def test_la_perdante_repasse_des_que_le_solde_suffit(client):
    """Le refus n'a rien laissé derrière lui : un achat suffit pour confirmer."""
    premiere, seconde = (_brouillon(client, rang) for rang in (1, 2))
    _confirmer(premiere, resultats := {})
    _confirmer(seconde, resultats)
    assert isinstance(resultats[seconde.id], services.CampagneRefusee)

    credits.acheter(client.id, COUT, reference="OM-2")
    _confirmer(seconde, resultats)

    assert resultats[seconde.id].etat == Campagne.Etat.EN_ATTENTE
    cache, recalcule = _soldes(client)
    assert cache == recalcule == credits.Solde(SOLDE + COUT, 2 * COUT)


def test_un_verrou_tenu_trop_longtemps_refuse_avec_un_message_clair(client, monkeypatch):
    """Une autre opération tient le solde : la confirmation n'attend pas sans fin."""
    monkeypatch.setattr(credits, "ATTENTE_RESERVATION_MS", 300)
    campagne = _brouillon(client, 1)
    verrou_pris, relacher = threading.Event(), threading.Event()

    def tenir_le_solde():
        try:
            with transaction.atomic():
                CompteCredit.objects.select_for_update().get(client_id=client.id)
                verrou_pris.set()
                assert relacher.wait(10)
        finally:
            connection.close()

    fil = threading.Thread(target=tenir_le_solde)
    fil.start()
    assert verrou_pris.wait(10)
    try:
        with pytest.raises(services.CampagneRefusee) as refus:
            services.confirmer(campagne, auteur=None, destinataires_lus=DESTINATAIRES, cout_lu=COUT)
    finally:
        relacher.set()
        fil.join(10)

    assert str(refus.value) == (
        "Le solde est occupé par une autre opération en cours. Réessayez dans un instant."
    )
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.BROUILLON
    assert not Destinataire.objects.filter(campagne=campagne).exists()
    assert _soldes(client)[0] == credits.Solde(SOLDE, 0)

    # Le verrou rendu, la même confirmation passe.
    services.confirmer(campagne, auteur=None, destinataires_lus=DESTINATAIRES, cout_lu=COUT)
    assert _soldes(client)[0] == credits.Solde(SOLDE, COUT)


def test_le_delai_d_attente_est_rendu_a_la_transaction(client):
    """La borne ne vaut que pour la prise du verrou de la réservation."""
    with transaction.atomic(), connection.cursor() as curseur:
        curseur.execute("SET LOCAL lock_timeout = '42s'")
        credits.reserver(client.id, PRIX, campagne_id=uuid.uuid7())
        curseur.execute("SHOW lock_timeout")
        assert curseur.fetchone()[0] == "42s"
