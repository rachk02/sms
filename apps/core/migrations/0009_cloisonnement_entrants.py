"""Cloisonnement par ligne de telecom_message_entrant.

Un message entrant non encore rapproché d'un client a un client_id nul : aucun
client ne le voit. Seuls les workers, qui contournent la politique, l'écrivent
et le rapprochent.
"""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0008_destination_des_evenements"),
        ("telecom", "0003_messages_entrants"),
    ]

    operations = [cloisonner("telecom_message_entrant")]
