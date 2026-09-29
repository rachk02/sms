"""Pose du client courant sur la connexion PostgreSQL.

Le réglage `app.client` est lu par les politiques de cloisonnement par ligne.
Il est toujours posé local à la transaction (`set_config(..., true)`) : il
disparaît au commit ou au rollback, et ne peut donc pas fuir d'une requête à
la suivante sur une connexion réutilisée. Hors transaction, il ne durerait que
le temps d'une instruction ; c'est pourquoi `poser_client` exige d'être appelé
dans un bloc `transaction.atomic`.
"""

from uuid import UUID

from django.db import connection

REGLAGE = "app.client"


def poser_client(client_id: UUID | None) -> None:
    if not connection.in_atomic_block:
        raise RuntimeError("poser_client doit être appelé dans une transaction.")
    valeur = str(client_id) if client_id else ""
    with connection.cursor() as curseur:
        curseur.execute("SELECT set_config(%s, %s, true)", [REGLAGE, valeur])


def client_pose() -> str:
    with connection.cursor() as curseur:
        curseur.execute("SELECT current_setting(%s, true)", [REGLAGE])
        return curseur.fetchone()[0] or ""
