"""Cloisonnement par ligne des exports de contacts."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0021_cloisonnement_segments"),
        ("contacts", "0007_export_et_recherche"),
    ]

    operations = [cloisonner("contacts_export")]