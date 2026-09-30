"""Cloisonnement par ligne des modèles de message."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0022_cloisonnement_exports"),
        ("campagnes", "0001_modeles"),
    ]

    operations = [cloisonner("campagnes_modele")]