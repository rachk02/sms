"""Pause, reprise, annulation et clôture d'une campagne (T-64).

Sous le rôle du worker, comme l'ordonnanceur et les workers d'envoi ; un
connecteur simulé par réseau. Le worker d'envoi est appelé message par message
sur ce que la boîte d'envoi a publié, comme le ferait la file.
"""

import uuid

import pytest
from django.db.models import Count
from django.test import override_settings
from django.utils import timezone

from apps.campagnes import diffusion, selectors, services
from apps.campagnes.models import Campagne
from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts import listes
from apps.contacts.services import creer_contact
from apps.core.etats import Etat
from apps.core.models import EvenementSortant
from apps.credits import services as credits
from apps.credits.models import Ecriture, Reservation
from apps.journal.models import Evenement, Message
from apps.journal.services import soumettre
from apps.telecom import debit
from apps.telecom.models import Connecteur, Prefixe
from apps.telecom.passerelles import passerelle_pour
from workers.envoi import WorkerEnvoi

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("reglages"),
]

PRIX = 20
ACHAT = 1_000_000


@pytest.fixture
def reglages():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=PRIX, SMS_NUMERO_COURT="36000"):
        yield


@pytest.fixture
def client(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        [Prefixe(pays="BF", prefixe="70", operateur="ORANGE")], ignore_conflicts=True
    )
    Connecteur.objects.create(
        nom=f"simule-{uuid.uuid4().hex[:6]}",
        operateur="ORANGE",
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        debit_max=100,
    )
    client = creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    credits.acheter(client.id, ACHAT, reference="OM-1")
    yield client
    Prefixe.objects.filter(pays="BF").delete()


@pytest.fixture
def worker(monkeypatch):
    """Le worker d'envoi ; un créneau de débit pris est compté."""
    creneaux = []

    def creneau(connecteur):
        creneaux.append(connecteur.id)
        return 0.0

    monkeypatch.setattr(debit, "creneau", creneau)
    envoi = WorkerEnvoi(files=[])
    envoi.creneaux = creneaux
    return envoi


def _confirmee(client, nombre: int) -> Campagne:
    liste = listes.creer(client=client, nom="Clients")
    listes.ajouter(
        liste,
        [
            creer_contact(client=client, numero=f"70{n:06d}", origine="SAISIE", prenom=f"P{n}").id
            for n in range(nombre)
        ],
    )
    campagne = services.creer(
        client=client,
        auteur=None,
        nom="Promo",
        texte="Bonjour {prenom|cher client}, promotion.",
        listes=[liste.id],
    )
    vu = services.apercu(campagne)
    return services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )


def _en_file(campagne, depuis=0) -> list[dict]:
    """Les messages de file publiés pour la campagne, dans l'ordre."""
    ids = {
        str(i) for i in Message.objects.filter(campagne_id=campagne.id).values_list("id", flat=True)
    }
    publies = EvenementSortant.objects.filter(nom__contains="envoi.").order_by("id")
    return [e.donnees for e in publies if e.donnees.get("message_id") in ids][depuis:]


def _etats(campagne) -> dict:
    return dict(
        Message.objects.filter(campagne_id=campagne.id)
        .values_list("etat")
        .annotate(n=Count("id"))
        .values_list("etat", "n")
    )


def _consomme(client) -> int:
    return -sum(
        Ecriture.objects.filter(client_id=client.id, type=Ecriture.Type.CONSOMMATION).values_list(
            "montant", flat=True
        )
    )


def _soumissions_par_message(campagne) -> set:
    ids = Message.objects.filter(campagne_id=campagne.id).values_list("id", flat=True)
    return set(
        Evenement.objects.filter(message_id__in=ids, etat=Etat.SOUMIS)
        .values_list("message_id")
        .annotate(n=Count("id"))
        .values_list("n", flat=True)
    )


def test_une_pause_arrete_la_soumission_sans_prendre_de_creneau(client, worker):
    campagne = _confirmee(client, 6)
    assert diffusion.publier() == 6
    file = _en_file(campagne)
    for donnees in file[:2]:
        worker._soumettre(donnees, avec_creneau=True)
    services.mettre_en_pause(campagne, auteur=None)
    for donnees in file[2:]:
        worker._soumettre(donnees, avec_creneau=True)
    assert _etats(campagne) == {Etat.SOUMIS: 2, Etat.EN_ATTENTE: 4}
    assert len(worker.creneaux) == 2  # les messages retenus n'ont pris aucun créneau
    assert diffusion.publier() == 0  # en pause, rien ne se publie ni ne se remet en file
    bilan = selectors.bilan(Campagne.objects.get(id=campagne.id))
    assert (bilan.partis, bilan.non_soumis, bilan.en_soumission) == (2, 4, 0)
    assert _consomme(client) == 2 * PRIX


def test_une_pause_avant_toute_publication_compte_les_destinataires_non_publies(client):
    campagne = _confirmee(client, 5)
    diffusion.publier_lot(campagne.id, taille=2)
    services.mettre_en_pause(campagne, auteur=None)
    assert diffusion.publier() == 0
    bilan = selectors.bilan(Campagne.objects.get(id=campagne.id))
    assert (bilan.partis, bilan.non_soumis) == (0, 5)


def test_pause_puis_reprise_sans_perte_ni_doublon(client, worker):
    campagne = _confirmee(client, 6)
    diffusion.publier()
    premiers = _en_file(campagne)
    worker._soumettre(premiers[0])
    services.mettre_en_pause(campagne, auteur=None)
    for donnees in premiers[1:]:
        worker._soumettre(donnees)
    services.reprendre(campagne, auteur=None)
    assert Campagne.objects.get(id=campagne.id).etat == Campagne.Etat.EN_COURS
    assert diffusion.publier() == 5  # les cinq retenus, remis en file
    remis = _en_file(campagne, depuis=len(premiers))
    assert len(remis) == 5
    # Les copies d'avant la pause, relivrées par la file, ne partent pas une seconde fois.
    for donnees in premiers + remis:
        worker._soumettre(donnees)
    assert _etats(campagne) == {Etat.SOUMIS: 6}
    assert _soumissions_par_message(campagne) == {1}
    diffusion.publier()  # clôture
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.TERMINEE and campagne.terminee_le
    assert campagne.cout_reel == 6 * PRIX == _consomme(client)
    reservation = Reservation.objects.get(campagne_id=campagne.id)
    assert reservation.etat == Reservation.Etat.REGLEE
    assert credits.solde(client.id) == credits.solde_recalcule(client.id)


def test_une_annulation_libere_exactement_les_credits_des_messages_non_partis(client, worker):
    campagne = _confirmee(client, 8)
    assert campagne.cout_estime == 8 * PRIX
    diffusion.publier_lot(campagne.id, taille=5)
    file = _en_file(campagne)
    for donnees in file[:3]:
        worker._soumettre(donnees)
    services.annuler(campagne, auteur=None)
    worker._soumettre(file[3], avec_creneau=True)  # une copie tirée après l'annulation
    assert worker.creneaux == []  # annulée sans prendre de créneau
    diffusion.publier()
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.ANNULEE and campagne.terminee_le
    assert _etats(campagne) == {Etat.SOUMIS: 3, Etat.ANNULE: 2}
    assert campagne.cout_reel == 3 * PRIX == _consomme(client)
    reservation = Reservation.objects.get(campagne_id=campagne.id)
    assert reservation.etat == Reservation.Etat.LIBEREE and reservation.montant_restant == 0
    solde = credits.solde(client.id)
    assert solde == credits.solde_recalcule(client.id)
    assert (solde.acquis, solde.reserve) == (ACHAT - 3 * PRIX, 0)
    # La dernière copie en file, relivrée après la clôture, s'acquitte sans rien toucher.
    worker._soumettre(file[4])
    assert _consomme(client) == 3 * PRIX
    bilan = selectors.bilan(campagne)
    assert (bilan.partis, bilan.annules, bilan.non_soumis) == (3, 5, 0)


def test_une_annulation_n_annule_jamais_un_message_dont_la_soumission_a_commence(client, worker):
    campagne = _confirmee(client, 3)
    diffusion.publier()
    file = _en_file(campagne)
    en_vol = Message.objects.get(id=file[0]["message_id"])
    # Un worker a posé son marqueur et appelle la passerelle.
    Message.objects.filter(id=en_vol.id).update(soumission_debutee_le=timezone.now())
    services.annuler(campagne, auteur=None)
    diffusion.publier()
    campagne.refresh_from_db()
    assert Message.objects.get(id=en_vol.id).etat == Etat.RECU
    assert campagne.terminee_le is None  # la clôture attend son sort
    assert Reservation.objects.get(campagne_id=campagne.id).etat == Reservation.Etat.ACTIVE
    worker._soumettre(file[0])  # la soumission s'achève et se règle
    diffusion.publier()
    campagne.refresh_from_db()
    assert _etats(campagne) == {Etat.SOUMIS: 1, Etat.ANNULE: 2}
    assert campagne.terminee_le and campagne.cout_reel == PRIX == _consomme(client)
    assert credits.solde(client.id) == credits.solde_recalcule(client.id)


def test_la_cloture_regle_un_message_soumis_que_le_worker_n_a_pas_encore_regle(client, worker):
    campagne = _confirmee(client, 2)
    diffusion.publier()
    file = _en_file(campagne)
    worker._soumettre(file[0])
    # Le second est soumis, puis le worker s'interrompt avant d'écrire la consommation.
    message = Message.objects.get(id=file[1]["message_id"])
    soumettre(message, passerelle_pour(Connecteur.objects.get(operateur="ORANGE")))
    assert _consomme(client) == PRIX
    diffusion.publier()
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.TERMINEE
    assert campagne.cout_reel == 2 * PRIX == _consomme(client)
    worker._soumettre(file[1])  # le worker reprend : rien de plus n'est consommé
    assert _consomme(client) == 2 * PRIX
    assert credits.solde(client.id) == credits.solde_recalcule(client.id)


def test_une_campagne_annulee_avant_sa_publication_rend_tout_sur_le_champ(client):
    campagne = _confirmee(client, 4)
    services.annuler(campagne, auteur=None)
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.ANNULEE and campagne.terminee_le
    assert campagne.cout_reel == 0
    assert credits.solde(client.id).disponible == ACHAT
    assert diffusion.publier() == 0
    assert not Message.objects.filter(campagne_id=campagne.id).exists()


def test_les_transitions_interdites_sont_refusees(client):
    campagne = _confirmee(client, 1)
    with pytest.raises(services.CampagneRefusee):
        services.reprendre(campagne, auteur=None)
    services.annuler(campagne, auteur=None)
    for action in (services.mettre_en_pause, services.reprendre, services.annuler):
        with pytest.raises(services.CampagneRefusee):
            action(campagne, auteur=None)


def test_une_campagne_terminee_ne_s_annule_plus(client, worker):
    campagne = _confirmee(client, 2)
    diffusion.publier()
    for donnees in _en_file(campagne):
        worker._soumettre(donnees)
    diffusion.publier()
    assert Campagne.objects.get(id=campagne.id).etat == Campagne.Etat.TERMINEE
    with pytest.raises(services.CampagneRefusee):
        services.annuler(campagne, auteur=None)
