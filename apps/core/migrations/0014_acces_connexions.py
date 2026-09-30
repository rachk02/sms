"""Accès au journal des connexions : écriture par le rôle applicatif, lecture
réservée au back-office. Les tentatives précèdent toute identification du
client et portent sur tous les comptes."""

from django.db import migrations

ACTIVER = [
    "ALTER TABLE comptes_connexion ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE comptes_connexion FORCE ROW LEVEL SECURITY",
    "CREATE POLICY comptes_connexion_ecriture ON comptes_connexion FOR INSERT WITH CHECK (true)",
]

DESACTIVER = [
    "DROP POLICY comptes_connexion_ecriture ON comptes_connexion",
    "ALTER TABLE comptes_connexion NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE comptes_connexion DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0013_cloisonnement_numeros_test"),
        ("comptes", "0005_journal_connexions"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
