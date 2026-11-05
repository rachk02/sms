"""Détection du STOP entrant, T-69."""

import importlib
import types

import pytest
from django.apps import apps as registre
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.telecom.models import Connecteur, MessageEntrant, MotCle, Operateur
from apps.telecom.mots_cles import correspond, normaliser, reconnaitre
from apps.telecom.services import enregistrer_entrant

migration = importlib.import_module("apps.telecom.migrations.0005_mots_cles")


def _desinscription(texte: str) -> bool:
    return any(correspond(texte, mot) for mot in migration.DESINSCRIPTION)


def test_la_plateforme_seme_les_trois_mots_cles_de_la_fiche():
    assert migration.DESINSCRIPTION == ("STOP", "ARRET", "ARRETER")


def test_normaliser_retire_accents_casse_et_signes():
    assert normaliser("  Arrêt !! ") == "ARRET"
    assert normaliser("s'il vous plaît") == "S IL VOUS PLAIT"


@pytest.mark.parametrize(
    "texte",
    [
        "stop",
        "STOP",
        "Stop.",
        "arrêt",
        "ARRET",
        "stop svp",
        "Arrêter",
        "  stop  ",
        "STOP!!!",
        "S T O P",
        "S.T.O.P",
        "Stop, merci",
        "svp stop",
        "Arrêt s'il vous plaît",
        "stop\n",
    ],
)
def test_les_formes_d_une_desinscription_sont_reconnues(texte):
    assert _desinscription(texte)


@pytest.mark.parametrize(
    "texte",
    [
        "Je ne veux pas que ça s'arrête, stop ou encore ?",
        "Le car fait stop à Koudougou",
        "arrêt de bus",
        "STOPPE",
        "stop stop la promo est géniale",
        "merci",
        "",
    ],
)
def test_un_mot_cle_pris_dans_une_phrase_n_est_pas_une_desinscription(texte):
    assert not _desinscription(texte)


def _connecteur():
    return Connecteur.objects.create(
        nom="simule-orange", operateur=Operateur.ORANGE, type=Connecteur.Type.SIMULE
    )


def test_la_migration_seme_les_mots_cles_de_desinscription(en_tant_que_worker):
    MotCle.objects.all().delete()
    editeur = types.SimpleNamespace(connection=types.SimpleNamespace(alias="default"))
    migration.semer(registre, editeur)
    assert set(MotCle.objects.values_list("mot_cle", "action")) == {
        (mot, MotCle.Action.DESINSCRIPTION) for mot in migration.DESINSCRIPTION
    }


def test_la_table_se_modifie_sans_redeploiement(en_tant_que_worker):
    MotCle.objects.get_or_create(mot_cle="STOP", action=MotCle.Action.DESINSCRIPTION)
    assert reconnaitre("Désabonner") is None

    ajout = MotCle.objects.create(mot_cle="DESABONNER", action=MotCle.Action.DESINSCRIPTION)
    assert reconnaitre("Désabonner svp") == ajout

    ajout.delete()
    assert reconnaitre("Désabonner") is None
    assert reconnaitre("stop").mot_cle == "STOP"


def test_un_mot_cle_d_un_client_ne_vaut_pas_pour_la_plateforme(en_tant_que_worker):
    client = creer_client(raison_sociale="Boutique SA", forme=Client.Forme.SOCIETE)
    MotCle.objects.create(client=client, mot_cle="FIN", action=MotCle.Action.DESINSCRIPTION)
    assert reconnaitre("fin") is None


def test_l_entrant_porte_le_mot_cle_reconnu_et_le_rejeu_ne_change_rien(en_tant_que_worker):
    MotCle.objects.get_or_create(mot_cle="ARRET", action=MotCle.Action.DESINSCRIPTION)
    connecteur = _connecteur()
    commun = {
        "connecteur_id": connecteur.id,
        "numero_expediteur": "22670123456",
        "numero_destination": "36123",
        "recu_le": timezone.now(),
    }

    entrant, cree = enregistrer_entrant(reference="mo-1", texte="Arrêt.", **commun)
    assert cree and entrant.mot_cle_reconnu == "ARRET"
    assert entrant.texte == "Arrêt."  # le texte reçu est gardé mot pour mot

    rejoue, cree = enregistrer_entrant(reference="mo-1", texte="Arrêt.", **commun)
    assert not cree and rejoue.id == entrant.id
    assert MessageEntrant.objects.count() == 1

    phrase, _ = enregistrer_entrant(reference="mo-2", texte="On s'arrête où ?", **commun)
    assert phrase.mot_cle_reconnu == ""
