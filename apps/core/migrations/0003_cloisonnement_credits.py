"""Cloisonnement par ligne des tables du module credits."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0002_cloisonnement_journal"),
        ("credits", "0001_initial"),
    ]

    operations = [
        cloisonner("credits_registre"),
        cloisonner("credits_compte"),
        cloisonner("credits_reservation"),
    ]
