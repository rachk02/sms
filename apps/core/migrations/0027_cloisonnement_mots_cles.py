"""Cloisonnement des mots-clés des SMS entrants.

Un client lit ses mots-clés et ceux de la plateforme (client_id nul), et
n'écrit que les siens. Les mots-clés de la plateforme ne sont tenus que par le
back-office et les workers, qui contournent ces politiques.
"""

from django.db import migrations

from apps.core.cloisonnement import CLIENT_COURANT

ACTIVER = [
    "ALTER TABLE telecom_mot_cle ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE telecom_mot_cle FORCE ROW LEVEL SECURITY",
    "CREATE POLICY telecom_mot_cle_lecture ON telecom_mot_cle FOR SELECT"
    f" USING (client_id = {CLIENT_COURANT} OR client_id IS NULL)",
    "CREATE POLICY telecom_mot_cle_ajout ON telecom_mot_cle FOR INSERT"
    f" WITH CHECK (client_id = {CLIENT_COURANT})",
    "CREATE POLICY telecom_mot_cle_modification ON telecom_mot_cle FOR UPDATE"
    f" USING (client_id = {CLIENT_COURANT}) WITH CHECK (client_id = {CLIENT_COURANT})",
    "CREATE POLICY telecom_mot_cle_suppression ON telecom_mot_cle FOR DELETE"
    f" USING (client_id = {CLIENT_COURANT})",
]

DESACTIVER = [
    "DROP POLICY telecom_mot_cle_suppression ON telecom_mot_cle",
    "DROP POLICY telecom_mot_cle_modification ON telecom_mot_cle",
    "DROP POLICY telecom_mot_cle_ajout ON telecom_mot_cle",
    "DROP POLICY telecom_mot_cle_lecture ON telecom_mot_cle",
    "ALTER TABLE telecom_mot_cle NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE telecom_mot_cle DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0026_cloisonnement_compteur"),
        ("telecom", "0005_mots_cles"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
