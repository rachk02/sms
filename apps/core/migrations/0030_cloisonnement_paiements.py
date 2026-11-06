"""Cloisonnement par ligne des transactions de paiement : un client ne voit que
les siennes. Les rappels et leur vérification passent par les workers, qui
contournent la politique."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0029_cloisonnement_grilles"),
        ("paiements", "0001_transactions"),
    ]

    operations = [cloisonner("paiements_transaction")]
