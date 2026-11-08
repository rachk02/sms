"""Cloisonnement par ligne des réponses d'idempotence de l'API (T-108)."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0040_cloisonnement_cles_api"),
        ("api", "0001_initial"),
    ]

    operations = [cloisonner("api_idempotence")]
