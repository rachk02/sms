"""Dictionnaire anti-spam : commun à tous, lu par chacun.

Le portail le lit pour examiner le texte d'une campagne à la composition. La
table ne s'écrit que par le back-office, qui contourne la politique ; le rôle
applicatif n'y écrit jamais.
"""

from django.db import migrations

TABLE = "conformite_mot_proscrit"

ACTIVER = [
    f"ALTER TABLE {TABLE} ENABLE ROW LEVEL SECURITY",
    f"ALTER TABLE {TABLE} FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY {TABLE}_lecture ON {TABLE} FOR SELECT USING (true)",
]

DESACTIVER = [
    f"DROP POLICY {TABLE}_lecture ON {TABLE}",
    f"ALTER TABLE {TABLE} NO FORCE ROW LEVEL SECURITY",
    f"ALTER TABLE {TABLE} DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0034_cloisonnement_routes"),
        ("conformite", "0004_mots_proscrits"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
