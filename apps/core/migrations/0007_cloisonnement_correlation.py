"""telecom_correlation et telecom_accuse_orphelin : réservées aux workers.

Tables de plateforme sans client_id, écrites et lues par les workers à la
soumission et à l'accusé. Sécurité par ligne forcée, sans politique : le rôle
applicatif n'y voit rien.
"""

from django.db import migrations

TABLES = ("telecom_correlation", "telecom_accuse_orphelin")


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0006_cloisonnement_telecom"),
        ("telecom", "0002_correlation"),
    ]

    operations = [
        migrations.RunSQL(
            sql=[
                instruction
                for table in TABLES
                for instruction in (
                    f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY",
                    f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY",
                )
            ],
            reverse_sql=[
                instruction
                for table in TABLES
                for instruction in (
                    f"ALTER TABLE {table} NO FORCE ROW LEVEL SECURITY",
                    f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY",
                )
            ],
        )
    ]
