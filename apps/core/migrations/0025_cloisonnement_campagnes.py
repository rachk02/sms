"""Cloisonnement par ligne des campagnes et de leurs destinataires figés."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0024_cloisonnement_expediteurs"),
        ("campagnes", "0002_campagnes_et_destinataires"),
    ]

    operations = [cloisonner("campagnes_campagne"), cloisonner("campagnes_destinataire")]