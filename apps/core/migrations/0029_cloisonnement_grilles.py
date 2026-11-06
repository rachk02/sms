"""Grilles tarifaires : la grille publique se lit par tous, une grille négociée
par son seul client.

Les versions et les paliers n'ont pas de `client_id` : leur politique passe par
la ligne parente, elle-même soumise à sa politique. Un client ne lit donc ni les
versions ni les prix de la grille négociée d'un autre. Ces tables ne s'écrivent
que par le back-office et les workers, qui contournent ces politiques ; le rôle
applicatif n'y écrit jamais, faute de politique d'écriture.
"""

from django.db import migrations

from apps.core.cloisonnement import CLIENT_COURANT

LECTURES = {
    "credits_grille": f"client_id IS NULL OR client_id = {CLIENT_COURANT}",
    "credits_grille_version": (
        "EXISTS (SELECT 1 FROM credits_grille g WHERE g.id = credits_grille_version.grille_id)"
    ),
    "credits_grille_palier": (
        "EXISTS (SELECT 1 FROM credits_grille_version v"
        " WHERE v.id = credits_grille_palier.version_id)"
    ),
}

ACTIVER = [
    instruction
    for table, condition in LECTURES.items()
    for instruction in (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_lecture ON {table} FOR SELECT USING ({condition})",
    )
]

DESACTIVER = [
    instruction
    for table in reversed(LECTURES)
    for instruction in (
        f"DROP POLICY {table}_lecture ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    )
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0028_cloisonnement_conformite"),
        ("credits", "0005_grilles_tarifaires"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
