"""Accès à la boîte d'envoi des événements.

Ce n'est pas une table métier : elle porte des événements de tous les clients,
et d'autres qui n'appartiennent à aucun. Le rôle applicatif doit pouvoir y
écrire, dans la transaction de l'écriture métier, sans jamais pouvoir y lire.
Une seule politique, pour INSERT : toute autre opération voit zéro ligne. Le
relais passe par le rôle worker, qui contourne la politique.
"""

from django.db import migrations

ACTIVER = [
    "ALTER TABLE core_evenement_sortant ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE core_evenement_sortant FORCE ROW LEVEL SECURITY",
    "CREATE POLICY core_evenement_sortant_ecriture ON core_evenement_sortant"
    " FOR INSERT WITH CHECK (true)",
]

DESACTIVER = [
    "DROP POLICY core_evenement_sortant_ecriture ON core_evenement_sortant",
    "ALTER TABLE core_evenement_sortant NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE core_evenement_sortant DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [("core", "0004_evenements")]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
