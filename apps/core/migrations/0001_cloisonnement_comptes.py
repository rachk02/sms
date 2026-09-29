"""Cloisonnement par ligne des tables du module comptes.

Migration dédiée, séparée de toute migration de schéma, comme le veut la charte.
`comptes_utilisateur` n'est pas cloisonnée : la connexion la lit par courriel
avant que le client soit connu.
"""

from django.db import migrations

from apps.core.cloisonnement import cloisonner


class Migration(migrations.Migration):
    dependencies = [("comptes", "0001_initial")]

    operations = [
        # Une ligne de comptes_client est son propre client : la politique porte sur id.
        cloisonner("comptes_client", colonne="id"),
    ]
