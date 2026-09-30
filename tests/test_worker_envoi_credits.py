"""Le worker d'envoi solde le crédit de chaque message qu'il soumet.

Message réel au journal, connecteur simulé : la soumission consomme sur la
réservation de la campagne ; une relivraison ne consomme pas une seconde fois ;
un message refusé avant le réseau ne consomme rien.
"""

import uuid

import pytest
from django.conf import settings
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.etats import Etat
from apps.core.models import EvenementSortant
from apps.credits import services as credits
from apps.credits.models import Ecriture
from apps.journal.services import enregistrer_message, mettre_en_file
from apps.telecom import correlation
from apps.telecom.models import Connecteur
from workers.envoi import WorkerEnvoi

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def client(en_tant_que_worker):
    client = creer_client(
        raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    credits.acheter(client.id, 10_000, reference="OM-0001")
    Connecteur.objects.create(
        nom=f"simule-{timezone.now().timestamp()}",
        operateur="ORANGE",
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
    )
    return client


def _message(client, campagne_id, expediteur):
    return enregistrer_message(
        client=client,
        numero_normalise="+22670123456",
        operateur="ORANGE",
        expediteur_libelle=expediteur,
        texte="Promotion",
        cout_unitaire=20,
        campagne_id=campagne_id,
    )


def _envoyer(worker, message):
    donnees = {"message_id": str(message.id), "date_reception": message.date_reception.isoformat()}
    worker._soumettre(donnees)
    message.refresh_from_db()
    if message.reference_smsc:
        correlation._redis().delete(
            correlation.cle_redis(message.connecteur_id, message.reference_smsc)
        )
    return message


def test_la_soumission_consomme_une_fois_sur_la_reservation_de_la_campagne(client):
    campagne_id = uuid.uuid7()
    reservation = credits.reserver(client.id, 1_000, campagne_id=campagne_id)
    worker = WorkerEnvoi()
    envoye = _envoyer(worker, _message(client, campagne_id, settings.SMS_NUMERO_COURT))
    _envoyer(worker, envoye)  # relivré par le courtier
    refuse = _envoyer(worker, _message(client, campagne_id, "INCONNU"))
    assert (envoye.etat, refuse.etat) == (Etat.SOUMIS, Etat.REJETE)
    consommations = Ecriture.objects.filter(client_id=client.id, type="CONSOMMATION")
    assert list(consommations.values_list("montant", "reference")) == [
        (-20, credits.reference_message(envoye.id))
    ]
    reservation.refresh_from_db()
    assert reservation.montant_restant == 980


def test_un_message_se_depose_dans_la_file_de_son_reseau(client):
    message = _message(client, None, settings.SMS_NUMERO_COURT)
    mettre_en_file(message, prioritaire=True)
    evenement = EvenementSortant.objects.get(cle_idempotence=f"envoi:{message.id}")
    assert evenement.nom.endswith("envoi.prioritaire.orange") and evenement.echange == "sms"
    assert evenement.donnees["message_id"] == str(message.id)
