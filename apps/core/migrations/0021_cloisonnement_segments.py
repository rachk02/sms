"""Cloisonnement par ligne des segments."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0020_cloisonnement_champs"),
        ("contacts", "0006_segments"),
    ]

    operations = [cloisonner("contacts_segment")]