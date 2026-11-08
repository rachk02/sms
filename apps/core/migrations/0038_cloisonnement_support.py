"""Billetterie (T-102) : tickets, messages et pièces cloisonnés par client.

Le client lit et écrit les siens dans la transaction de sa requête ; les
agents les lisent et les traitent par le back-office, et l'ordonnanceur clôt
les tickets échus, tous deux hors des politiques.
"""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0037_regle_plafond_compte"),
        ("support", "0001_initial"),
    ]

    operations = [
        cloisonner("support_ticket"),
        cloisonner("support_message"),
        cloisonner("support_piece"),
    ]
