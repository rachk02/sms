"""Fenêtre de soumission et reprise, T-25.

Un worker tué entre l'écriture du marqueur et la réponse de la passerelle ne
doit pas produire de doublon au redémarrage. La coupure est simulée par une
exception levée au moment choisi ; le rejeu est un second appel à `soumettre`,
comme le ferait le courtier en relivrant le message.
"""

import pytest
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.etats import Etat
from apps.journal.models import Evenement, Message
from apps.journal.services import MOTIF_SORT_INCONNU, enregistrer_message, soumettre
from apps.telecom import correlation
from apps.telecom.jasmin import Jasmin
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import passerelle_pour

pytestmark = pytest.mark.django_db(transaction=True)


class Coupure(BaseException):
    """La mort du processus : rien après elle ne s'exécute."""


@pytest.fixture
def message(en_tant_que_worker):
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    return enregistrer_message(
        client=client,
        numero_normalise="+22670123456",
        operateur="ORANGE",
        expediteur_libelle="EXEMPLE",
        texte="Votre code est 4821",
        cout_unitaire=10,
    )


def _connecteur(type_=Connecteur.Type.SIMULE):
    return Connecteur.objects.create(
        nom=f"c-{timezone.now().timestamp()}", operateur=Operateur.ORANGE, type=type_
    )


def _nettoyer(passerelle, message):
    reference = Message.objects.get(id=message.id).reference_smsc
    if reference:
        correlation._redis().delete(correlation.cle_redis(passerelle.connecteur.id, reference))


def test_un_worker_tue_apres_la_soumission_ne_produit_pas_de_doublon(message):
    passerelle = passerelle_pour(_connecteur())
    soumettre_reel = passerelle.soumettre

    def soumettre_puis_mourir(soumission):
        soumettre_reel(soumission)  # la passerelle a bien reçu le message
        raise Coupure

    passerelle.soumettre = soumettre_puis_mourir
    with pytest.raises(Coupure):
        soumettre(message, passerelle)
    coupe = Message.objects.get(id=message.id)
    assert coupe.soumission_debutee_le is not None and coupe.date_soumission is None

    passerelle.soumettre = soumettre_reel  # le worker redémarre
    repris = soumettre(message, passerelle)
    _nettoyer(passerelle, message)

    assert len(passerelle.soumissions) == 1, "le message a été soumis deux fois"
    assert repris.etat == Etat.SOUMIS
    assert repris.reference_smsc == next(iter(passerelle.soumissions))
    evenement = Evenement.objects.get(message_id=message.id, etat=Etat.SOUMIS)
    assert "reprise" in evenement.details


def test_un_worker_tue_avant_l_appel_soumet_une_fois_au_redemarrage(message):
    passerelle = passerelle_pour(_connecteur())
    soumettre_reel = passerelle.soumettre

    def mourir_avant(soumission):
        raise Coupure

    passerelle.soumettre = mourir_avant
    with pytest.raises(Coupure):
        soumettre(message, passerelle)
    assert passerelle.soumissions == {}

    passerelle.soumettre = soumettre_reel
    repris = soumettre(message, passerelle)
    _nettoyer(passerelle, message)
    assert len(passerelle.soumissions) == 1
    assert repris.etat == Etat.SOUMIS


def test_un_message_deja_soumis_relivre_n_est_pas_resoumis(message):
    passerelle = passerelle_pour(_connecteur())
    soumettre(message, passerelle)
    soumettre(message, passerelle)
    _nettoyer(passerelle, message)
    assert len(passerelle.soumissions) == 1
    assert Evenement.objects.filter(message_id=message.id, etat=Etat.SOUMIS).count() == 1


def test_une_passerelle_qui_ne_sait_pas_retrouver_une_soumission_ne_resoumet_pas(message):
    passerelle = Jasmin(_connecteur(Connecteur.Type.SMPP), url="http://127.0.0.1:9")
    appels = []

    def soumettre_puis_mourir(soumission):
        appels.append(soumission)
        raise Coupure

    passerelle.soumettre = soumettre_puis_mourir
    with pytest.raises(Coupure):
        soumettre(message, passerelle)

    repris = soumettre(message, passerelle)
    assert len(appels) == 1, "resoumis alors que le sort de la première soumission est inconnu"
    assert (repris.etat, repris.motif_echec) == (Etat.EN_FILE, MOTIF_SORT_INCONNU)


def test_soumettre_refuse_une_transaction_deja_ouverte(message):
    from django.db import transaction

    passerelle = passerelle_pour(_connecteur())
    with pytest.raises(RuntimeError, match="hors transaction"), transaction.atomic():
        soumettre(message, passerelle)
