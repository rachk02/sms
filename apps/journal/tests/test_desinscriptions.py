"""Rapprochement du STOP avec son émetteur, T-70."""

import json
import logging
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts.models import ListeNoire
from apps.core.etats import Etat
from apps.core.models import EvenementSortant
from apps.core.redis import client_redis
from apps.journal.models import Message
from apps.journal.services import (
    CLE_DESINSCRIPTIONS_SANS_EMETTEUR,
    DESINSCRIPTION_RAPPROCHEE,
    enregistrer_message,
    rapprocher_desinscription,
)
from apps.telecom.models import Connecteur, MessageEntrant, MotCle, Operateur
from apps.telecom.services import DESINSCRIPTION_RECUE, enregistrer_entrant
from workers.accuses import _desinscrire, _rapprocher_desinscription

NUMERO = "+22670123456"
RECU_LE = timezone.now().replace(microsecond=0)


@pytest.fixture(autouse=True)
def mois_precedent(django_db_blocker):
    """La fenêtre peut remonter au mois précédent : sa partition doit exister."""
    from django.db import connections

    with django_db_blocker.unblock(), connections["proprietaire"].cursor() as curseur:
        for table in ("journal_message", "journal_evenement"):
            curseur.execute(
                "SELECT journal_creer_partition(%s::regclass,"
                " (date_trunc('month', now()) - interval '1 month')::date)",
                [table],
            )


def _client(nom):
    return creer_client(raison_sociale=nom, forme=Client.Forme.SOCIETE)


def _envoi(client, *, soumis_il_y_a=None, recu_il_y_a=None, etat=Etat.LIVRE):
    """Un message vers NUMERO, soumis `soumis_il_y_a` avant la réception du STOP,
    ou jamais soumis si None."""
    recu_il_y_a = recu_il_y_a or soumis_il_y_a or timedelta(hours=1)
    message = enregistrer_message(
        client=client,
        numero_normalise=NUMERO,
        operateur=Operateur.ORANGE,
        expediteur_libelle="BOUTIQUE",
        texte="Promotion",
        cout_unitaire=25,
        date_reception=RECU_LE - recu_il_y_a,
    )
    Message.objects.filter(id=message.id).update(
        etat=etat, date_soumission=RECU_LE - soumis_il_y_a if soumis_il_y_a else None
    )
    return message


def _entrant(texte="Stop svp", expediteur="22670123456"):
    connecteur = Connecteur.objects.create(
        nom=f"simule-{uuid.uuid4()}", operateur=Operateur.ORANGE, type=Connecteur.Type.SIMULE
    )
    return MessageEntrant.objects.create(
        connecteur=connecteur,
        reference=str(uuid.uuid4()),
        numero_expediteur=expediteur,
        numero_destination="36123",
        texte=texte,
        mot_cle_reconnu="STOP",
        recu_le=RECU_LE,
    )


def _rapprocher(entrant):
    return rapprocher_desinscription(
        entrant_id=entrant.id,
        numero_expediteur=entrant.numero_expediteur,
        texte=entrant.texte,
        recu_le=entrant.recu_le,
    )


def _evenement(sortant):
    """Corps d'un événement tel que le relais le publie sur le bus."""

    class Livre:
        message_id = sortant.cle_idempotence
        body = json.dumps(
            {
                "nom": sortant.nom,
                "client_id": str(sortant.client_id) if sortant.client_id else None,
                "objet_id": str(sortant.objet_id),
                "donnees": sortant.donnees,
                "cle_idempotence": sortant.cle_idempotence,
            }
        ).encode()

    return Livre


def _rapprochees(entrant):
    return EvenementSortant.objects.filter(nom=DESINSCRIPTION_RAPPROCHEE, objet_id=entrant.id)


def test_un_stop_vaut_pour_chaque_client_qui_a_ecrit_au_numero(en_tant_que_worker):
    a, b, c, ancien, refuse = (_client(f"Client {i} SA") for i in "ABCDE")
    _envoi(a, soumis_il_y_a=timedelta(hours=1))
    _envoi(b, soumis_il_y_a=timedelta(hours=30))
    _envoi(c, soumis_il_y_a=timedelta(hours=71, minutes=59))
    _envoi(ancien, soumis_il_y_a=timedelta(hours=73))
    _envoi(refuse, etat=Etat.REJETE)  # refusé avant le réseau : jamais soumis
    entrant = _entrant()

    retenus = _rapprocher(entrant)

    assert set(retenus) == {a.id, b.id, c.id}
    assert {e.client_id for e in _rapprochees(entrant)} == {a.id, b.id, c.id}
    entrant.refresh_from_db()
    assert entrant.client_id is None and entrant.traite_le is not None

    for sortant in _rapprochees(entrant):
        _desinscrire(_evenement(sortant))
    entrees = ListeNoire.objects.filter(numero_normalise=NUMERO)
    assert {e.client_id for e in entrees} == {a.id, b.id, c.id}
    assert {(e.origine, e.message_origine) for e in entrees} == {
        (ListeNoire.Origine.STOP_SMS, "Stop svp")
    }


def test_rejouer_chaque_evenement_ne_cree_rien_de_plus(en_tant_que_worker):
    clients = [_client(f"Client {i} SA") for i in "ABC"]
    for client in clients:
        _envoi(client, soumis_il_y_a=timedelta(hours=2))
    entrant = _entrant()
    recu = EvenementSortant(
        nom=DESINSCRIPTION_RECUE,
        objet_id=entrant.id,
        donnees={
            "numero_expediteur": entrant.numero_expediteur,
            "texte": entrant.texte,
            "recu_le": entrant.recu_le.isoformat(),
        },
        cle_idempotence=f"desinscription:{entrant.id}",
    )

    _rapprocher_desinscription(_evenement(recu))
    _rapprocher_desinscription(_evenement(recu))
    assert _rapprochees(entrant).count() == 3

    for sortant in _rapprochees(entrant):
        _desinscrire(_evenement(sortant))
        _desinscrire(_evenement(sortant))
    assert ListeNoire.objects.filter(numero_normalise=NUMERO).count() == 3


def test_un_seul_client_recoit_le_stop_et_le_message_entrant(en_tant_que_worker):
    seul = _client("Seul SA")
    _envoi(seul, soumis_il_y_a=timedelta(minutes=5))
    entrant = _entrant()

    assert _rapprocher(entrant) == [seul.id]
    entrant.refresh_from_db()
    assert entrant.client_id == seul.id


def test_une_campagne_reprise_tard_compte_a_sa_soumission(en_tant_que_worker):
    reprise = _client("Reprise SA")
    _envoi(reprise, recu_il_y_a=timedelta(days=5), soumis_il_y_a=timedelta(hours=1))
    assert _rapprocher(_entrant()) == [reprise.id]


def test_un_stop_sans_historique_est_garde_et_signale(en_tant_que_worker, caplog):
    avant = int(client_redis().get(CLE_DESINSCRIPTIONS_SANS_EMETTEUR) or 0)
    entrant = _entrant()

    with caplog.at_level(logging.WARNING, logger="apps.journal.services"):
        assert _rapprocher(entrant) == []

    entrant.refresh_from_db()
    assert entrant.traite_le is not None and entrant.client_id is None
    assert entrant.texte == "Stop svp"
    assert "Désinscription sans envoi correspondant" in caplog.text
    assert int(client_redis().get(CLE_DESINSCRIPTIONS_SANS_EMETTEUR)) == avant + 1
    assert not ListeNoire.objects.filter(numero_normalise=NUMERO).exists()


def test_un_expediteur_illisible_ne_bloque_pas_la_file(en_tant_que_worker):
    entrant = _entrant(expediteur="36123")  # un numéro court ne se normalise pas
    assert _rapprocher(entrant) == []
    entrant.refresh_from_db()
    assert entrant.traite_le is not None


def test_seul_un_stop_nouveau_est_annonce(en_tant_que_worker):
    MotCle.objects.get_or_create(mot_cle="STOP", action=MotCle.Action.DESINSCRIPTION)
    connecteur = Connecteur.objects.create(
        nom="simule-annonce", operateur=Operateur.ORANGE, type=Connecteur.Type.SIMULE
    )
    commun = {
        "connecteur_id": connecteur.id,
        "numero_expediteur": "22670123456",
        "numero_destination": "36123",
        "recu_le": RECU_LE,
    }

    stop, _ = enregistrer_entrant(reference="mo-stop", texte="STOP", **commun)
    enregistrer_entrant(reference="mo-stop", texte="STOP", **commun)  # rappel rejoué
    enregistrer_entrant(reference="mo-phrase", texte="Je veux bien, merci", **commun)

    annonces = EvenementSortant.objects.filter(nom=DESINSCRIPTION_RECUE)
    assert [(a.objet_id, a.donnees["texte"]) for a in annonces] == [(stop.id, "STOP")]


@pytest.mark.parametrize("decalage", [timedelta(hours=1), timedelta(hours=-1)])
def test_la_fenetre_part_de_la_reception_du_stop(en_tant_que_worker, decalage):
    """Un STOP traité en retard (paliers de réessai) garde sa fenêtre : celle qui
    précède sa réception, ni plus tard ni plus tôt."""
    client = _client("Fenetre SA")
    _envoi(client, soumis_il_y_a=decalage)  # une heure avant le STOP, ou une heure après
    attendu = [client.id] if decalage > timedelta(0) else []
    assert _rapprocher(_entrant()) == attendu
