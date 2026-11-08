"""Cloisonnement par ligne des messages du bac à sable (T-113)."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0042_cloisonnement_webhooks"),
        ("api", "0003_messages_essai"),
    ]

    operations = [cloisonner("api_message_essai")]
