"""Incidents de la page d'état (T-103) : réservés aux workers et au back-office.

Tables de la plateforme, sans `client_id`. Sécurité par ligne activée et forcée,
sans aucune politique : le rôle applicatif n'en voit aucune ligne. Le
back-office les écrit, l'ordonnanceur les lit pour publier la page.
"""

from django.db import migrations

TABLES = ("support_incident", "support_publication_incident")

ACTIVER = [
    instruction
    for table in TABLES
    for instruction in (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
    )
]

DESACTIVER = [
    instruction
    for table in TABLES
    for instruction in (
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    )
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0038_cloisonnement_support"),
        ("support", "0002_incidents"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
