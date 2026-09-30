"""Cloisonnement par ligne des listes de contacts et de leurs membres."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0017_cloisonnement_imports"),
        ("contacts", "0003_listes"),
    ]

    operations = [cloisonner("contacts_liste"), cloisonner("contacts_liste_contact")]