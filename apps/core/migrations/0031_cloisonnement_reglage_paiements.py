"""Réglages du rechargement : lus par tous, pour annoncer le montant TTC et le
pas d'achat, écrits par le back-office seul, qui contourne la politique."""

from django.db import migrations

TABLE = "paiements_reglage"

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
        ("core", "0030_cloisonnement_paiements"),
        ("paiements", "0002_reglage_et_initiateur"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
