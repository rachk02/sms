"""Cloisonnement par ligne, T-06. Les tests les plus importants du lot 1.

Ils passent par les vraies connexions et les vrais rôles : `default` est le
rôle applicatif, `proprietaire` possède les tables, `worker` contourne la
politique. Une table de démonstration portant `client_id` est créée le temps
des tests, avec exactement les instructions des migrations de `core`.
"""

import uuid

import pytest
from django.db import ProgrammingError, connections, transaction

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.cloisonnement import instructions_cloisonnement, poser_client

TOUTES = ["default", "proprietaire", "worker"]
TABLE = "test_cloisonnement_donnee"


def _executer(sql, parametres=None, using="default"):
    with connections[using].cursor() as curseur:
        curseur.execute(sql, parametres)
        return curseur.fetchall() if curseur.description else None


def _ids_visibles(table, using="default", colonne="client_id"):
    return {ligne[0] for ligne in _executer(f"SELECT {colonne} FROM {table}", using=using)}


@pytest.fixture
def deux_clients(transactional_db):
    return (
        creer_client(raison_sociale="Premier", forme=Client.Forme.SOCIETE),
        creer_client(raison_sociale="Second", forme=Client.Forme.SOCIETE),
    )


@pytest.fixture
def table_cloisonnee(deux_clients):
    activer, _ = instructions_cloisonnement(TABLE)
    with transaction.atomic(using="proprietaire"):
        _executer(
            f"CREATE TABLE {TABLE} (id uuid PRIMARY KEY, client_id uuid NOT NULL, valeur text)",
            using="proprietaire",
        )
        for instruction in activer:
            _executer(instruction, using="proprietaire")
    for client in deux_clients:
        with transaction.atomic():
            poser_client(client.id)
            _executer(
                f"INSERT INTO {TABLE} VALUES (%s, %s, %s)",
                [uuid.uuid7(), client.id, client.raison_sociale],
            )
    yield deux_clients
    _executer(f"DROP TABLE {TABLE}", using="proprietaire")


@pytest.mark.django_db(transaction=True, databases=TOUTES)
def test_un_client_pose_ne_voit_aucune_ligne_d_un_autre_client(table_cloisonnee):
    premier, second = table_cloisonnee
    with transaction.atomic():
        poser_client(premier.id)
        assert _ids_visibles(TABLE) == {premier.id}
        assert _ids_visibles("comptes_client", colonne="id") == {premier.id}
        assert set(Client.objects.values_list("id", flat=True)) == {premier.id}


@pytest.mark.django_db(transaction=True, databases=TOUTES)
def test_sans_client_pose_une_requete_voit_zero_ligne_et_non_toutes(table_cloisonnee):
    # Réglage jamais posé sur cette transaction.
    assert _ids_visibles(TABLE) == set()
    assert Client.objects.count() == 0


@pytest.mark.django_db(transaction=True, databases=TOUTES)
def test_un_reglage_pose_puis_vide_rend_zero_ligne_sans_erreur(table_cloisonnee):
    premier, _ = table_cloisonnee
    with transaction.atomic():
        poser_client(premier.id)
        assert _ids_visibles(TABLE) == {premier.id}
    # Après le commit, la même connexion garde le réglage défini mais vide.
    assert _ids_visibles(TABLE) == set()
    with transaction.atomic():
        poser_client(None)
        assert _ids_visibles(TABLE) == set()


@pytest.mark.django_db(transaction=True, databases=TOUTES)
def test_on_ne_peut_pas_ecrire_une_ligne_pour_un_autre_client(table_cloisonnee):
    premier, second = table_cloisonnee
    with pytest.raises(ProgrammingError, match="row-level security"), transaction.atomic():
        poser_client(premier.id)
        _executer(f"INSERT INTO {TABLE} VALUES (%s, %s, 'intrus')", [uuid.uuid7(), second.id])


@pytest.mark.django_db(transaction=True, databases=TOUTES)
def test_le_proprietaire_des_tables_est_lui_aussi_soumis_a_la_politique(table_cloisonnee):
    assert _ids_visibles(TABLE, using="proprietaire") == set()
    assert _ids_visibles("comptes_client", using="proprietaire", colonne="id") == set()


@pytest.mark.django_db(transaction=True, databases=TOUTES)
def test_le_role_worker_contourne_la_politique(table_cloisonnee):
    premier, second = table_cloisonnee
    assert _ids_visibles(TABLE, using="worker") == {premier.id, second.id}


@pytest.mark.django_db(transaction=True, databases=TOUTES)
def test_le_role_applicatif_ne_peut_rien_faire_de_plus_que_la_politique(table_cloisonnee):
    super_utilisateur, contourne = _executer(
        "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname = current_user"
    )[0]
    assert not super_utilisateur and not contourne

    with pytest.raises(ProgrammingError, match="must be owner"), transaction.atomic():
        _executer("ALTER TABLE comptes_client DISABLE ROW LEVEL SECURITY")
    with pytest.raises(ProgrammingError, match="must be owner"), transaction.atomic():
        _executer("DROP POLICY comptes_client_cloisonnement ON comptes_client")
    proprietaire = connections["proprietaire"].settings_dict["USER"]
    with pytest.raises(ProgrammingError, match="permission denied"), transaction.atomic():
        _executer(f'SET ROLE "{proprietaire}"')


@pytest.mark.django_db(transaction=True, databases=TOUTES)
def test_comptes_utilisateur_reste_lisible_sans_client_pose(deux_clients):
    # La connexion cherche l'utilisateur par son courriel avant que le client soit connu.
    _executer("SELECT count(*) FROM comptes_utilisateur")
    etat = _executer("SELECT relrowsecurity FROM pg_class WHERE relname = 'comptes_utilisateur'")[
        0
    ][0]
    assert etat is False
