"""Cloisonnement des contacts, et accès à la table des préfixes.

telecom_prefixe est une table de plateforme : le portail la lit (estimation de
la répartition par opérateur), personne n'y écrit hors du back-office. Une
seule politique, en lecture : le rôle applicatif lit, n'écrit rien ; le
back-office et les workers, qui contournent la politique, la tiennent.
"""

from django.db import migrations

from apps.core.cloisonnement import cloisonner

PREFIXES = [
    "ALTER TABLE telecom_prefixe ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE telecom_prefixe FORCE ROW LEVEL SECURITY",
    "CREATE POLICY telecom_prefixe_lecture ON telecom_prefixe FOR SELECT USING (true)",
]

RETIRER = [
    "DROP POLICY telecom_prefixe_lecture ON telecom_prefixe",
    "ALTER TABLE telecom_prefixe NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE telecom_prefixe DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0015_audit"),
        ("contacts", "0001_initial"),
        ("telecom", "0004_prefixes"),
    ]

    operations = [
        cloisonner("contacts_contact"),
        migrations.RunSQL(sql=PREFIXES, reverse_sql=RETIRER),
    ]