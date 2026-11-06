"""Constat des messages restés sans accusé (lot 7, préalable à T-78).

Un message SOUMIS dont aucun accusé n'est arrivé au terme de la validité de la
corrélation passe SANS_ACCUSE : passé ce terme, plus aucun accusé ne peut lui
être rattaché. C'est ce constat qui permet au règlement à la livraison de
conclure une campagne.
"""

from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.etats import Etat
from apps.journal import services
from apps.journal.models import Evenement, Message
from apps.journal.services import appliquer_accuse, enregistrer_message, soumettre
from apps.telecom import correlation
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import passerelle_pour
from workers import ordonnanceur

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def passerelle(en_tant_que_worker):
    connecteur = Connecteur.objects.create(
        nom=f"c-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SIMULE,
        parametres={"taux_echec": 0},
    )
    return passerelle_pour(connecteur)


@pytest.fixture
def client(passerelle):
    return creer_client(
        raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )


def _soumis(client, passerelle, numero="+22670123456"):
    message = enregistrer_message(
        client=client,
        numero_normalise=numero,
        operateur="ORANGE",
        expediteur_libelle=settings.SMS_NUMERO_COURT,
        texte="Bonjour",
        cout_unitaire=10,
    )
    return soumettre(message, passerelle)


def _terme(message):
    return message.date_soumission + correlation.validite()


def test_un_message_sans_accuse_au_terme_de_la_validite_est_constate(client, passerelle):
    message = _soumis(client, passerelle)

    assert services.constater_sans_accuse(_terme(message) - timedelta(minutes=1)) == []
    assert Message.objects.get(id=message.id).etat == Etat.SOUMIS

    instant = _terme(message) + timedelta(minutes=1)
    assert services.constater_sans_accuse(instant) == [(message.id, message.date_reception)]
    assert Message.objects.get(id=message.id).etat == Etat.SANS_ACCUSE
    dernier = Evenement.objects.filter(message_id=message.id).order_by("-horodatage").first()
    assert (dernier.etat, dernier.source, dernier.horodatage) == (
        Etat.SANS_ACCUSE,
        Evenement.Source.WORKER,
        instant,
    )

    # Un second passage ne constate rien de plus.
    assert services.constater_sans_accuse(instant) == []


def test_un_message_livre_n_est_pas_constate(client, passerelle):
    message = _soumis(client, passerelle)
    appliquer_accuse(passerelle.accuses_dus()[0])

    assert services.constater_sans_accuse(_terme(message) + timedelta(hours=1)) == []
    assert Message.objects.get(id=message.id).etat == Etat.LIVRE


def test_un_accuse_arrive_apres_le_constat_est_refuse(client, passerelle):
    message = _soumis(client, passerelle)
    accuse = passerelle.accuses_dus()[0]
    services.constater_sans_accuse(_terme(message) + timedelta(minutes=1))

    assert appliquer_accuse(accuse) is False
    assert Message.objects.get(id=message.id).etat == Etat.SANS_ACCUSE


def test_l_ordonnanceur_constate_par_lots_jusqu_a_epuisement(client, passerelle, monkeypatch):
    monkeypatch.setattr(services, "TAILLE_SANS_ACCUSE", 2)
    messages = [_soumis(client, passerelle, f"+2267012345{rang}") for rang in range(5)]
    instant = max(_terme(m) for m in messages) + timedelta(minutes=1)

    assert ordonnanceur.constater_sans_accuse(instant, lots_max=2) == 4  # borné à deux lots
    assert ordonnanceur.constater_sans_accuse(instant) == 1
    assert set(Message.objects.values_list("etat", flat=True)) == {Etat.SANS_ACCUSE}


def test_le_seuil_suit_la_validite_reglee(client, passerelle, settings):
    settings.VALIDITE_SMS_HEURES = 1
    message = _soumis(client, passerelle)
    une_heure_et_marge = message.date_soumission + timedelta(hours=1) + correlation.MARGE

    assert services.constater_sans_accuse(une_heure_et_marge - timedelta(minutes=1)) == []
    assert len(services.constater_sans_accuse(une_heure_et_marge + timedelta(minutes=1))) == 1
