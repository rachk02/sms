"""Lissage d'une campagne par paliers (T-66).

Le lissage règle la publication, le débit de l'opérateur règle la soumission :
le plus contraignant des deux s'applique. Ici, le débit de l'opérateur en
temps réel et les paliers ; les trois heures sur horloge simulée sont dans
tests/test_lissage_trois_heures.py, plus long.
"""

import uuid
from collections import Counter
from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.campagnes import diffusion, services
from apps.campagnes.models import Campagne
from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts.models import Contact, Liste, ListeContact
from apps.core.models import EvenementSortant
from apps.credits import services as credits
from apps.journal.models import Message
from apps.telecom.models import Connecteur, Prefixe
from workers.envoi import WorkerEnvoi

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("reglages"),
]

PRIX = 20


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
    credits.acheter(client.id, 10_000_000, reference="OM-1")
    yield client
    Prefixe.objects.filter(pays="BF").delete()


def _connecteur(debit: int) -> Connecteur:
    return Connecteur.objects.create(
        nom=f"simule-{uuid.uuid4().hex[:6]}",
        operateur="ORANGE",
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        debit_max=debit,
    )


def _liste(client, nombre: int) -> Liste:
    liste = Liste.objects.create(client=client, nom="Clients", nombre_contacts=nombre)
    contacts = Contact.objects.bulk_create(
        Contact(
            client=client,
            numero_normalise=f"+22670{n:06d}",
            numero_origine=str(n),
            operateur_presume="ORANGE",
            origine="SAISIE",
        )
        for n in range(nombre)
    )
    ListeContact.objects.bulk_create(
        ListeContact(liste=liste, contact=c, client=client) for c in contacts
    )
    return liste


def _confirmee(client, nombre: int, **options) -> Campagne:
    campagne = services.creer(
        client=client,
        auteur=None,
        nom="Promo",
        texte="Promotion",
        listes=[_liste(client, nombre).id],
        **options,
    )
    vu = services.apercu(campagne)
    return services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )


class Publies:
    """Les messages de file publiés depuis le dernier appel."""

    def __init__(self):
        self.dernier = 0

    def nouveaux(self) -> list[dict]:
        publies = list(
            EvenementSortant.objects.filter(id__gt=self.dernier, nom__contains="envoi.")
            .order_by("id")
            .values_list("id", "donnees")
        )
        if publies:
            self.dernier = publies[-1][0]
        return [donnees for _, donnees in publies]


def test_le_debit_de_l_operateur_l_emporte_sur_un_lissage_plus_large(client):
    _connecteur(debit=5)
    campagne = _confirmee(client, 20, lissage_heures=1)
    # Un lissage plus large que l'opérateur : 600 par minute, soit 10 par seconde.
    Campagne.objects.filter(id=campagne.id).update(lissage_par_minute=600)
    publies = Publies()
    assert diffusion.publier() == 20  # le lissage ne retient rien
    worker = WorkerEnvoi(files=[])
    for donnees in publies.nouveaux():
        worker._soumettre(donnees, avec_creneau=True)
    instants = sorted(
        Message.objects.filter(campagne_id=campagne.id).values_list("date_soumission", flat=True)
    )
    duree = (instants[-1] - instants[0]).total_seconds()
    assert duree >= 19 / 5 - 0.2  # cinq par seconde, pas dix
    par_seconde = Counter(int((i - instants[0]).total_seconds()) for i in instants)
    assert max(par_seconde.values()) <= 5


def test_une_pause_ne_produit_pas_de_rafale_a_la_reprise(client):
    campagne = _confirmee(client, 600, lissage_heures=1)  # 10 par minute
    debut = timezone.now()
    assert diffusion.publier_lot(campagne.id, maintenant=debut) == 10
    assert diffusion.publier_lot(campagne.id, maintenant=debut + timedelta(seconds=30)) == 0
    services.mettre_en_pause(campagne, auteur=None)
    services.reprendre(campagne, auteur=None)
    plus_tard = debut + timedelta(minutes=30)
    assert diffusion.publier(maintenant=plus_tard) == 10  # un palier, pas trente
    assert diffusion.publier(maintenant=plus_tard + timedelta(seconds=59)) == 0
    assert diffusion.publier(maintenant=plus_tard + timedelta(minutes=1)) == 10


def test_une_campagne_non_lissee_part_d_un_coup(client):
    campagne = _confirmee(client, 50)
    assert campagne.lissage_par_minute is None
    assert diffusion.publier() == 50


def test_une_duree_d_etalement_inconnue_est_refusee(client):
    with pytest.raises(services.CampagneRefusee, match="étalement"):
        services.creer(
            client=client,
            auteur=None,
            nom="Promo",
            texte="Promotion",
            listes=[_liste(client, 1).id],
            lissage_heures=5,
        )


def test_une_occurrence_herite_du_lissage_de_sa_campagne_recurrente(client):
    from apps.campagnes import planification

    campagne = services.creer(
        client=client,
        auteur=None,
        nom="Promo",
        texte="Promotion",
        listes=[_liste(client, 120).id],
        planifiee_pour=timezone.now() + timedelta(hours=1),
        recurrence_jours=7,
        plafond=100_000,
        lissage_heures=2,
    )
    vu = services.apercu(campagne)
    patron = services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )
    planification.planifier(patron.prochaine_occurrence)
    occurrence = Campagne.objects.get(parent=patron)
    assert (occurrence.lissage_duree_minutes, occurrence.lissage_par_minute) == (120, 1)
