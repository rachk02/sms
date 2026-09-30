"""Cloisonnement par ligne des imports de contacts, de leurs rejets et de la
table de transit."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0016_cloisonnement_contacts"),
        ("contacts", "0002_imports"),
    ]

    operations = [
        cloisonner("contacts_import"),
        cloisonner("contacts_import_rejet"),
        cloisonner("contacts_import_transit"),
    ]