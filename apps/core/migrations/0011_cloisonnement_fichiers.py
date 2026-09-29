"""Cloisonnement par ligne de fichiers_fichier."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0010_acces_audit"),
        ("fichiers", "0001_initial"),
    ]

    operations = [cloisonner("fichiers_fichier")]
