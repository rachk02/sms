"""Cloisonnement par ligne des points de réception et des livraisons de
webhooks (T-111)."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0041_cloisonnement_idempotence"),
        ("api", "0002_webhooks"),
    ]

    operations = [cloisonner("api_point_reception"), cloisonner("api_livraison")]
