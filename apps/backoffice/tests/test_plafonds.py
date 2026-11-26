"""Plafond du compte et suspension automatique (T-101).

Un compte qui dépasse son plafond horaire reçoit un refus et une alerte part à
l'exploitation. Créer dix clés ne multiplie pas la limite : le plafond est
celui du compte. Au seuil critique, le compte est suspendu automatiquement.
"""

import importlib
from datetime import datetime, timedelta

import pytest
from django.utils import timezone

from apps.backoffice.services import reactiver_compte, suspendre_sur_alertes
from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.conformite import plafonds
from apps.conformite.models import AlerteFraude, RegleFraude
from apps.core.models import Audit, EvenementSortant
from apps.journal.models import Message
from apps.journal.tests.test_envoi_unitaire import _connecteur, _envoyer
from apps.telecom.passerelles import passerelle_pour

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

Code = RegleFraude.Code
Gravite = AlerteFraude.Gravite
MIDI = timezone.make_aware(datetime(2026, 11, 7, 12, 5))


@pytest.fixture(autouse=True)
def regle(transactional_db):
    """La règle semée par la migration, reposée après le vidage de la base."""
    migration = importlib.import_module("apps.core.migrations.0037_regle_plafond_compte")
    RegleFraude.objects.using("worker").update_or_create(
        code=migration.REGLE["code"], defaults=migration.REGLE
    )


def _compte(horaire, journalier=100_000):
    client = creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    Client.objects.filter(id=client.id).update(
        plafond_horaire=horaire, plafond_journalier=journalier
    )
    client.refresh_from_db()
    return client


def _essayer(client, fois, *, maintenant=MIDI, cle_api=None):
    """Le nombre d'essais acceptés sur `fois`."""
    acceptes = 0
    for _ in range(fois):
        try:
            plafonds.consommer(client, maintenant=maintenant, cle_api=cle_api)
            acceptes += 1
        except plafonds.PlafondAtteint:
            pass
    return acceptes


def test_dix_cles_ne_multiplient_pas_le_plafond(en_tant_que_worker):
    client = _compte(horaire=5)
    acceptes = sum(_essayer(client, 5, cle_api=f"cle-{rang}") for rang in range(10))
    assert acceptes == 5
    assert plafonds.essais(client.id, maintenant=MIDI)["horaire"] == 50
    # Une heure plus tard, le compte repart.
    assert _essayer(client, 1, maintenant=MIDI + timedelta(hours=1)) == 1


def test_le_plafond_journalier_vaut_aussi(en_tant_que_worker):
    client = _compte(horaire=10, journalier=12)
    assert _essayer(client, 10) == 10
    assert _essayer(client, 10, maintenant=MIDI + timedelta(hours=1)) == 2
    with pytest.raises(plafonds.PlafondAtteint, match="journalier"):
        plafonds.consommer(client, maintenant=MIDI + timedelta(hours=2))


def test_au_dela_du_plafond_l_envoi_est_refuse_et_l_exploitation_prevenue(en_tant_que_worker):
    # Plafond de trois : le seuil critique, à 200 %, tombe au sixième essai.
    client = _compte(horaire=3)
    passerelle = passerelle_pour(_connecteur())
    for rang in range(3):
        _envoyer(client, passerelle, f"Votre code : 482{rang}")
    with pytest.raises(plafonds.PlafondAtteint, match="Plafond horaire du compte atteint : 3"):
        _envoyer(client, passerelle, "Votre code : 4823")
    # Refusé avant toute écriture au journal.
    assert Message.objects.filter(client=client).count() == 3
    alerte = AlerteFraude.objects.get(client=client, regle=Code.PLAFOND_COMPTE)
    assert (alerte.gravite, alerte.valeur) == (Gravite.ALERTE, 4)
    assert alerte.details["periode"] == "horaire" and alerte.details["plafond"] == 3
    assert EvenementSortant.objects.filter(
        nom="conformite.fraude.alerte", objet_id=alerte.id
    ).exists()
    # Un refus suivant, sous le seuil critique, ne réécrit pas l'alerte.
    with pytest.raises(plafonds.PlafondAtteint):
        _envoyer(client, passerelle, "Votre code : 4824")
    alerte.refresh_from_db()
    assert (alerte.gravite, alerte.valeur) == (Gravite.ALERTE, 4)


def test_au_seuil_critique_le_compte_est_suspendu_une_fois(en_tant_que_worker):
    client = _compte(horaire=3)
    assert _essayer(client, 5) == 3
    alerte = AlerteFraude.objects.get(client=client, regle=Code.PLAFOND_COMPTE)
    assert alerte.gravite == Gravite.ALERTE
    assert suspendre_sur_alertes() == []

    # 200 % du plafond : six essais.
    _essayer(client, 1)
    alerte.refresh_from_db()
    assert (alerte.gravite, alerte.valeur) == (Gravite.CRITIQUE, 6)
    assert [c.id for c in suspendre_sur_alertes()] == [client.id]
    client.refresh_from_db()
    assert client.etat == Client.Etat.SUSPENDU
    trace = Audit.objects.get(action="comptes.suspension", objet_id=str(client.id))
    assert trace.auteur_id is None and "Suspension automatique" in trace.motif
    assert suspendre_sur_alertes() == []

    # Réactivé par un agent sans traiter l'alerte : un simple dépassement ne
    # resuspend pas, un nouveau franchissement du seuil critique, si.
    reactiver_compte(client, agent=None, motif="Client joint, test de charge")
    une_heure_apres = MIDI + timedelta(hours=1)
    _essayer(client, 4, maintenant=une_heure_apres)
    assert suspendre_sur_alertes() == []
    _essayer(client, 2, maintenant=une_heure_apres)
    assert [c.id for c in suspendre_sur_alertes()] == [client.id]
