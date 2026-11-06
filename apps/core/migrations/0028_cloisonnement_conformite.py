"""Fenêtre légale et jours fériés : communs à tous, lus par chacun.

Le portail lit la fenêtre pour annoncer l'heure de libération d'une campagne
en attente. Ces tables ne s'écrivent que par le back-office et les workers, qui
contournent ces politiques ; le rôle applicatif n'y écrit jamais.
"""

from django.db import migrations

TABLES = ("conformite_fenetre", "conformite_jour_ferie")

ACTIVER = [
    instruction
    for table in TABLES
    for instruction in (
        f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
        f"CREATE POLICY {table}_lecture ON {table} FOR SELECT USING (true)",
    )
]

DESACTIVER = [
    instruction
    for table in TABLES
    for instruction in (
        f"DROP POLICY {table}_lecture ON {table}",
        f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
        f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
    )
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0027_cloisonnement_mots_cles"),
        ("conformite", "0003_fenetre_et_jours_feries"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
