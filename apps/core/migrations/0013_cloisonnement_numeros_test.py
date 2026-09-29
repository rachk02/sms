"""Cloisonnement par ligne des numéros de test du bac à sable."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0012_cloisonnement_dossiers"),
        ("comptes", "0004_numeros_test"),
    ]

    operations = [cloisonner("comptes_numero_test")]
