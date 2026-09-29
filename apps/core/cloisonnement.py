"""Cloisonnement par ligne : pose du client courant et politiques PostgreSQL.

Le réglage `app.client` est lu par les politiques de cloisonnement par ligne.
Il est toujours posé local à la transaction (`set_config(..., true)`) : il
disparaît au commit ou au rollback, et ne peut donc pas fuir d'une requête à
la suivante sur une connexion réutilisée. Hors transaction, il ne durerait que
le temps d'une instruction ; c'est pourquoi `poser_client` exige d'être appelé
dans un bloc `transaction.atomic`.
"""

from uuid import UUID

from django.db import DEFAULT_DB_ALIAS, connections, migrations

REGLAGE = "app.client"

# `current_setting(..., true)` rend NULL au lieu de lever une erreur quand le
# réglage n'a jamais été posé, et NULLIF ramène à NULL la chaîne vide qu'il
# vaut après avoir été posé puis vidé. Dans les deux cas la comparaison est
# fausse : zéro ligne, jamais une erreur, jamais toutes les lignes.
CLIENT_COURANT = f"NULLIF(current_setting('{REGLAGE}', true), '')::uuid"


def poser_client(client_id: UUID | str | None, using: str = DEFAULT_DB_ALIAS) -> None:
    connexion = connections[using]
    if not connexion.in_atomic_block:
        raise RuntimeError("poser_client doit être appelé dans une transaction.")
    valeur = str(client_id) if client_id else ""
    with connexion.cursor() as curseur:
        curseur.execute("SELECT set_config(%s, %s, true)", [REGLAGE, valeur])


def client_pose(using: str = DEFAULT_DB_ALIAS) -> str:
    with connections[using].cursor() as curseur:
        curseur.execute("SELECT current_setting(%s, true)", [REGLAGE])
        return curseur.fetchone()[0] or ""


def instructions_cloisonnement(table: str, colonne: str = "client_id") -> tuple[list, list]:
    """Instructions SQL qui activent le cloisonnement d'une table, et leur inverse.

    FORCE soumet aussi le propriétaire de la table à la politique. WITH CHECK
    interdit d'écrire une ligne pour un autre client que le client posé.
    """
    politique = f"{table}_cloisonnement"
    condition = f"{colonne} = {CLIENT_COURANT}"
    activer = [
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {politique} ON {table} USING ({condition}) WITH CHECK ({condition})",
    ]
    desactiver = [
        f"DROP POLICY {politique} ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    ]
    return activer, desactiver


def cloisonner(table: str, colonne: str = "client_id") -> migrations.RunSQL:
    """Opération de migration, réservée aux migrations dédiées de `core`."""
    activer, desactiver = instructions_cloisonnement(table, colonne)
    return migrations.RunSQL(sql=activer, reverse_sql=desactiver)
