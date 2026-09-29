"""Partitionnement du journal, T-07."""

import uuid
from datetime import UTC, datetime

import pytest
from django.db import IntegrityError, ProgrammingError, connections, transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.cloisonnement import poser_client
from apps.journal.models import Etat, Evenement, Message
from apps.journal.services import creer_partitions_a_venir, enregistrer_message

pytestmark = pytest.mark.django_db(databases=["default", "proprietaire"])


def _debut_de_mois(decalage: int) -> datetime:
    maintenant = timezone.now()
    mois = maintenant.month - 1 + decalage
    return datetime(maintenant.year + mois // 12, mois % 12 + 1, 1, tzinfo=UTC)


def _nom_partition(table: str, decalage: int) -> str:
    return f"{table}_{_debut_de_mois(decalage):%Y_%m}"


def _partitions(table: str, using: str = "default") -> set[str]:
    with connections[using].cursor() as curseur:
        curseur.execute(
            "SELECT enfant.relname FROM pg_inherits"
            " JOIN pg_class enfant ON enfant.oid = pg_inherits.inhrelid"
            " WHERE pg_inherits.inhparent = %s::regclass",
            [table],
        )
        return {ligne[0] for ligne in curseur.fetchall()}


def _partition_de(message: Message) -> str:
    with connections["default"].cursor() as curseur:
        curseur.execute(
            "SELECT tableoid::regclass::text FROM journal_message WHERE id = %s", [message.id]
        )
        return curseur.fetchone()[0]


@pytest.fixture
def client_pose():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    poser_client(client.id)
    return client


def _message(client, **champs):
    valeurs = {
        "client": client,
        "numero_normalise": "+22670123456",
        "operateur": "ORANGE",
        "expediteur_libelle": "EXEMPLE",
        "texte": "Bonjour",
        "cout_unitaire": 10,
    }
    return enregistrer_message(**(valeurs | champs))


@pytest.mark.parametrize("table", ["journal_message", "journal_evenement"])
def test_trois_partitions_existent_apres_migration(table):
    attendues = {_nom_partition(table, decalage) for decalage in range(3)}
    assert attendues <= _partitions(table)


def test_une_insertion_du_mois_prochain_atterrit_dans_sa_partition(client_pose):
    mois_prochain = _debut_de_mois(1).replace(day=15)
    message = _message(client_pose, date_reception=mois_prochain)
    assert _partition_de(message) == _nom_partition("journal_message", 1)
    ce_mois = _message(client_pose, numero_normalise="+22670000000")
    assert _partition_de(ce_mois) == _nom_partition("journal_message", 0)


def test_un_message_sans_date_de_soumission_s_insere(client_pose):
    message = _message(client_pose)
    message = Message.objects.get(id=message.id)
    assert message.date_soumission is None
    assert message.etat == Etat.RECU
    assert (message.encodage, message.segments, message.cout_total) == ("GSM7", 1, 10)
    assert Evenement.objects.filter(message_id=message.id, etat=Etat.RECU).count() == 1


def test_une_requete_bornee_sur_un_mois_ne_lit_qu_une_partition(client_pose):
    debut, fin = _debut_de_mois(0), _debut_de_mois(1)
    with connections["default"].cursor() as curseur:
        curseur.execute(
            "EXPLAIN SELECT * FROM journal_message"
            " WHERE client_id = %s AND date_reception >= %s AND date_reception < %s",
            [client_pose.id, debut, fin],
        )
        plan = "\n".join(ligne[0] for ligne in curseur.fetchall())
    lues = {nom for nom in _partitions("journal_message") if nom in plan}
    assert lues == {_nom_partition("journal_message", 0)}


def test_un_contact_n_est_servi_qu_une_fois_par_campagne_meme_d_un_mois_a_l_autre(client_pose):
    campagne, contact = uuid.uuid7(), uuid.uuid7()
    _message(client_pose, campagne_id=campagne, contact_id=contact)
    with pytest.raises(IntegrityError), transaction.atomic():
        _message(
            client_pose,
            campagne_id=campagne,
            contact_id=contact,
            date_reception=_debut_de_mois(1).replace(day=3),
        )
    assert Message.objects.filter(campagne_id=campagne).count() == 1


def test_une_partition_n_est_pas_joignable_directement_par_le_role_applicatif(client_pose):
    partition = _nom_partition("journal_message", 0)
    with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic():
        with connections["default"].cursor() as curseur:
            curseur.execute(f"SELECT count(*) FROM {partition}")


def test_un_autre_client_ne_voit_pas_les_messages(client_pose):
    _message(client_pose)
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    poser_client(autre.id)
    assert Message.objects.count() == 0
    assert Evenement.objects.count() == 0


def test_la_creation_des_partitions_a_venir_est_idempotente():
    premieres = creer_partitions_a_venir(mois_d_avance=3)
    assert _nom_partition("journal_message", 3) in premieres
    assert creer_partitions_a_venir(mois_d_avance=3) == premieres
    # Créée dans la transaction du propriétaire, que le test ne valide pas.
    assert _nom_partition("journal_evenement", 3) in _partitions(
        "journal_evenement", using="proprietaire"
    )
