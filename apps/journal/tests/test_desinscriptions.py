"""Rapprochement du STOP avec son émetteur, T-70."""

import importlib
import json
import logging
import types
import uuid
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts.liste_noire import DESINSCRIPTION_INSCRITE
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
from workers.accuses import (
    _confirmer_desinscription,
    _desinscrire,
    _rapprocher_desinscription,
)

CONFIRMATION = importlib.import_module("apps.telecom.migrations.0005_mots_cles").CONFIRMATION

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


# Inscription et confirmation, T-71.


def _publies(nom):
    return list(EvenementSortant.objects.filter(nom=nom).order_by("cree_le", "id"))


@pytest.fixture
def chaine(en_tant_que_worker, settings, monkeypatch):
    """Un client sans aucun crédit, qui a écrit au numéro une heure avant son
    STOP, un réseau simulé, et le worker d'envoi sans attente de débit."""
    from apps.telecom import debit
    from workers.envoi import WorkerEnvoi

    settings.SMS_NUMERO_COURT = "36000"
    monkeypatch.setattr(debit, "creneau", lambda connecteur: 0.0)
    MotCle.objects.get_or_create(
        mot_cle="STOP",
        action=MotCle.Action.DESINSCRIPTION,
        defaults={"reponse_automatique": CONFIRMATION},
    )
    connecteur = Connecteur.objects.create(
        nom=f"simule-{uuid.uuid4().hex[:6]}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        debit_max=100,
    )
    client = creer_client(
        raison_sociale="Boutique SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    _envoi(client, soumis_il_y_a=timedelta(hours=1))
    return types.SimpleNamespace(client=client, connecteur=connecteur, worker=WorkerEnvoi(files=[]))


def _stop_jusqu_a_la_confirmation(chaine, texte="Stop !"):
    entrant, _ = enregistrer_entrant(
        connecteur_id=chaine.connecteur.id,
        reference=str(uuid.uuid4()),
        numero_expediteur="22670123456",
        numero_destination="36000",
        texte=texte,
        recu_le=RECU_LE,
    )
    for recu in _publies(DESINSCRIPTION_RECUE):
        _rapprocher_desinscription(_evenement(recu))
    for rapprochee in _publies(DESINSCRIPTION_RAPPROCHEE):
        _desinscrire(_evenement(rapprochee))
    for inscrite in _publies(DESINSCRIPTION_INSCRITE):
        _confirmer_desinscription(_evenement(inscrite))
        _confirmer_desinscription(_evenement(inscrite))  # relivrée par le courtier
    return entrant


def _systeme():
    return Message.objects.filter(nature=Message.Nature.SYSTEME)


def test_le_stop_inscrit_puis_confirme_gratuitement(chaine):
    _stop_jusqu_a_la_confirmation(chaine)

    entree = ListeNoire.objects.get(client=chaine.client, numero_normalise=NUMERO)
    assert entree.message_origine == "Stop !"  # mot pour mot : la preuve

    (confirmation,) = _systeme()
    assert confirmation.texte == (
        "Vous ne recevrez plus de SMS de BOUTIQUE. Votre désinscription est enregistrée."
    )
    assert (confirmation.encodage, confirmation.segments) == ("GSM7", 1)
    assert confirmation.expediteur_libelle == "36000"  # le numéro court
    assert (confirmation.cout_unitaire, confirmation.cout_total) == (0, 0)
    assert confirmation.client_id == chaine.client.id

    (file,) = EvenementSortant.objects.filter(objet_id=confirmation.id)
    assert file.nom.endswith("envoi.prioritaire.orange")


def test_la_confirmation_part_sans_credit_malgre_la_liste_noire(chaine):
    from apps.credits.models import Ecriture, Reservation

    _stop_jusqu_a_la_confirmation(chaine)
    (confirmation,) = _systeme()

    chaine.worker._soumettre(
        {
            "message_id": str(confirmation.id),
            "date_reception": confirmation.date_reception.isoformat(),
        }
    )

    confirmation = Message.objects.get(id=confirmation.id)
    assert confirmation.etat == Etat.SOUMIS
    assert not Ecriture.objects.filter(client_id=chaine.client.id).exists()
    assert not Reservation.objects.filter(client_id=chaine.client.id).exists()


def test_l_exemption_ne_vaut_que_pour_le_message_systeme(chaine):
    from apps.core import controles

    _stop_jusqu_a_la_confirmation(chaine)
    envoi = enregistrer_message(
        client=chaine.client,
        numero_normalise=NUMERO,
        operateur=Operateur.ORANGE,
        expediteur_libelle="36000",
        texte="Encore une promotion",
        cout_unitaire=25,
    )
    assert controles.refus(envoi) == "LISTE_NOIRE"
    assert controles.refus(_systeme().get()) is None


def test_l_inscription_est_annoncee_pour_le_webhook_du_client(chaine):
    entrant = _stop_jusqu_a_la_confirmation(chaine)

    (inscrite,) = _publies(DESINSCRIPTION_INSCRITE)
    assert inscrite.client_id == chaine.client.id
    assert inscrite.donnees["numero_normalise"] == NUMERO
    assert inscrite.donnees["message_origine"] == "Stop !"
    assert inscrite.donnees["entrant_id"] == str(entrant.id)
    assert inscrite.donnees["inscrit_le"]


def test_trois_clients_trois_confirmations_chacune_a_son_expediteur(chaine):
    for nom, expediteur in (("Autre SA", "AUTRE"), ("Troisieme SA", "TROISIEME")):
        client = creer_client(
            raison_sociale=nom, forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
        )
        message = _envoi(client, soumis_il_y_a=timedelta(hours=2))
        Message.objects.filter(id=message.id).update(expediteur_libelle=expediteur)

    _stop_jusqu_a_la_confirmation(chaine)

    assert {m.texte for m in _systeme()} == {
        CONFIRMATION.replace("{expediteur}", expediteur)
        for expediteur in ("AUTRE", "BOUTIQUE", "TROISIEME")
    }


def test_le_message_systeme_n_explique_pas_un_stop(chaine):
    """Une confirmation envoyée n'est pas un envoi du client : un second STOP
    sans autre envoi ne retrouve que le client de départ."""
    _stop_jusqu_a_la_confirmation(chaine)
    Message.objects.filter(nature=Message.Nature.CLIENT).update(
        date_soumission=RECU_LE - timedelta(hours=100)
    )
    Message.objects.filter(nature=Message.Nature.SYSTEME).update(
        date_soumission=RECU_LE - timedelta(minutes=1)
    )
    assert _rapprocher(_entrant()) == []
