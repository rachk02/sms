"""Annonces destinées aux webhooks du client, côté journal (T-111).

Le sort final d'un envoi hors campagne est annoncé une fois, et une seule ; un
message de campagne ou un message système ne l'est pas. Un SMS entrant revient
au client qui a écrit à ce numéro s'il est seul, et lui seul en est prévenu ;
un STOP est annoncé à chacun des clients rapprochés.
"""

import uuid
from datetime import timedelta

from django.db import transaction

from apps.core.etats import Etat
from apps.core.models import EvenementSortant
from apps.journal.models import Message
from apps.journal.services import (
    WEBHOOK_ENTRANT,
    _annoncer_sort,
    attribuer_entrant,
    changer_etat,
)
from apps.journal.tests.test_desinscriptions import (
    RECU_LE,
    _client,
    _entrant,
    _envoi,
    _rapprocher,
    mois_precedent,  # noqa: F401 (fixture automatique : la partition du mois précédent)
)

LIVRE, ECHEC = "client.message.livre", "client.message.echec"


def _annonces(nom, **filtres):
    return list(EvenementSortant.objects.filter(nom=nom, **filtres))


def _soumis(client):
    envoi = _envoi(client, soumis_il_y_a=timedelta(minutes=5), etat=Etat.SOUMIS)
    return Message.objects.get(id=envoi.id, date_reception=envoi.date_reception)


def test_le_sort_final_d_un_envoi_hors_campagne_est_annonce_une_fois(en_tant_que_worker):
    client = _client("Faso Riz SA")
    message = _soumis(client)
    with transaction.atomic():
        changer_etat(message, Etat.LIVRE, source="accuse", horodatage=RECU_LE)
    (annonce,) = _annonces(LIVRE, objet_id=message.id)
    assert annonce.client_id == client.id
    assert annonce.donnees["message_id"] == str(message.id)
    assert annonce.donnees["etat"] == Etat.LIVRE
    assert annonce.donnees["numero"] == message.numero_normalise
    # Une seconde annonce du même sort, par un rejeu, ne s'ajoute pas.
    _annoncer_sort(message, Etat.LIVRE, RECU_LE, "")
    assert len(_annonces(LIVRE, objet_id=message.id)) == 1


def test_un_echec_definitif_est_annonce_avec_son_motif(en_tant_que_worker):
    message = _soumis(_client("Faso Riz SA"))
    with transaction.atomic():
        changer_etat(
            message, Etat.NON_LIVRE, source="accuse", horodatage=RECU_LE, motif_echec="UNDELIV"
        )
    (annonce,) = _annonces(ECHEC, objet_id=message.id)
    assert annonce.donnees["etat"] == Etat.NON_LIVRE
    assert annonce.donnees["motif_echec"] == "UNDELIV"


def test_ni_campagne_ni_message_systeme_ni_etat_intermediaire(en_tant_que_worker):
    message = _soumis(_client("Faso Riz SA"))
    message.campagne_id = uuid.uuid7()
    _annoncer_sort(message, Etat.LIVRE, RECU_LE, "")
    message.campagne_id, message.nature = None, Message.Nature.SYSTEME
    _annoncer_sort(message, Etat.LIVRE, RECU_LE, "")
    message.nature = Message.Nature.CLIENT
    _annoncer_sort(message, Etat.SANS_ACCUSE, RECU_LE, "")
    assert not _annonces(LIVRE) and not _annonces(ECHEC)


def test_un_entrant_revient_au_seul_client_qui_a_ecrit_au_numero(en_tant_que_worker):
    seul = _client("Seul SA")
    _envoi(seul, soumis_il_y_a=timedelta(minutes=5))
    entrant = _entrant(texte="Merci, bien reçu")
    retenu = attribuer_entrant(
        entrant_id=entrant.id,
        numero_expediteur=entrant.numero_expediteur,
        texte=entrant.texte,
        recu_le=entrant.recu_le,
    )
    assert retenu == seul.id
    (annonce,) = _annonces(WEBHOOK_ENTRANT, objet_id=entrant.id)
    assert annonce.client_id == seul.id and annonce.donnees["texte"] == "Merci, bien reçu"
    entrant.refresh_from_db()
    assert entrant.client_id == seul.id


def test_un_entrant_ambigu_ou_sans_emetteur_n_est_annonce_a_personne(en_tant_que_worker):
    # Aucun client n'a écrit à ce numéro.
    orphelin = _entrant(texte="Bonjour")
    assert (
        attribuer_entrant(
            entrant_id=orphelin.id,
            numero_expediteur=orphelin.numero_expediteur,
            texte=orphelin.texte,
            recu_le=orphelin.recu_le,
        )
        is None
    )
    # Deux clients ont écrit à ce numéro : la réponse ne se montre à aucun.
    for nom in ("Premier SA", "Second SA"):
        _envoi(_client(nom), soumis_il_y_a=timedelta(minutes=5))
    ambigu = _entrant(texte="Qui êtes-vous ?")
    assert (
        attribuer_entrant(
            entrant_id=ambigu.id,
            numero_expediteur=ambigu.numero_expediteur,
            texte=ambigu.texte,
            recu_le=ambigu.recu_le,
        )
        is None
    )
    assert not _annonces(WEBHOOK_ENTRANT)


def test_un_stop_est_annonce_a_chaque_client_rapproche(en_tant_que_worker):
    clients = [_client(nom) for nom in ("Premier SA", "Second SA")]
    for client in clients:
        _envoi(client, soumis_il_y_a=timedelta(minutes=5))
    entrant = _entrant()
    _rapprocher(entrant)
    annonces = _annonces(WEBHOOK_ENTRANT, objet_id=entrant.id)
    assert sorted(a.client_id for a in annonces) == sorted(c.id for c in clients)
