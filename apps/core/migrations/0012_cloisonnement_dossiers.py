"""Cloisonnement par ligne des dossiers et des pièces d'onboarding."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0011_cloisonnement_fichiers"),
        ("comptes", "0002_dossiers_et_pieces"),
    ]

    operations = [cloisonner("comptes_dossier"), cloisonner("comptes_piece")]
