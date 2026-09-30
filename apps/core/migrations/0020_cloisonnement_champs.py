"""Cloisonnement par ligne des champs personnalisés."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0019_cloisonnement_liste_noire"),
        ("contacts", "0005_champs_personnalises"),
    ]

    operations = [cloisonner("contacts_champ")]