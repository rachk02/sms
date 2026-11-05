"""Cloisonnement par ligne des compteurs du suivi des campagnes."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0025_cloisonnement_campagnes"),
        ("journal", "0003_compteur"),
    ]

    operations = [cloisonner("journal_compteur")]
