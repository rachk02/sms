"""Cloisonnement par ligne des tables du module journal.

Posé sur les tables mères. Les partitions n'ont aucune politique propre : elles
ne sont joignables que par leur table mère, qui applique celle-ci à toute
lecture et à toute écriture.
"""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0001_cloisonnement_comptes"),
        ("journal", "0001_initial"),
    ]

    operations = [
        cloisonner("journal_message"),
        cloisonner("journal_evenement"),
        cloisonner("journal_destinataire"),
    ]
