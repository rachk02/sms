"""Accès au journal d'audit.

Toute action d'un administrateur, agent EL-IMMEKA ou administrateur d'un compte
client, y laisse une écriture : le rôle applicatif doit pouvoir y écrire. Il ne
peut pas y lire, les entrées portant sur tous les comptes. Une seule politique,
pour INSERT ; le back-office, qui contourne le cloisonnement, le consulte.
"""

from django.db import migrations

ACTIVER = [
    "ALTER TABLE conformite_audit ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE conformite_audit FORCE ROW LEVEL SECURITY",
    "CREATE POLICY conformite_audit_ecriture ON conformite_audit FOR INSERT WITH CHECK (true)",
]

DESACTIVER = [
    "DROP POLICY conformite_audit_ecriture ON conformite_audit",
    "ALTER TABLE conformite_audit NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE conformite_audit DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0009_cloisonnement_entrants"),
        ("conformite", "0001_initial"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
