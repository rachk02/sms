"""Cloisonnement de la liste noire, qui porte aussi les numéros de la plateforme.

Un client lit ses entrées et celles de la plateforme (client_id nul), dont il
doit tenir compte dans ses estimations ; il n'écrit, ne modifie et ne supprime
que les siennes. La liste de la plateforme n'est tenue que par le back-office
et les workers, qui contournent ces politiques.
"""

from django.db import migrations

from apps.core.cloisonnement import CLIENT_COURANT

ACTIVER = [
    "ALTER TABLE contacts_liste_noire ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE contacts_liste_noire FORCE ROW LEVEL SECURITY",
    "CREATE POLICY contacts_liste_noire_lecture ON contacts_liste_noire FOR SELECT"
    f" USING (client_id = {CLIENT_COURANT} OR client_id IS NULL)",
    "CREATE POLICY contacts_liste_noire_ajout ON contacts_liste_noire FOR INSERT"
    f" WITH CHECK (client_id = {CLIENT_COURANT})",
    "CREATE POLICY contacts_liste_noire_modification ON contacts_liste_noire FOR UPDATE"
    f" USING (client_id = {CLIENT_COURANT}) WITH CHECK (client_id = {CLIENT_COURANT})",
    "CREATE POLICY contacts_liste_noire_suppression ON contacts_liste_noire FOR DELETE"
    f" USING (client_id = {CLIENT_COURANT})",
]

DESACTIVER = [
    "DROP POLICY contacts_liste_noire_suppression ON contacts_liste_noire",
    "DROP POLICY contacts_liste_noire_modification ON contacts_liste_noire",
    "DROP POLICY contacts_liste_noire_ajout ON contacts_liste_noire",
    "DROP POLICY contacts_liste_noire_lecture ON contacts_liste_noire",
    "ALTER TABLE contacts_liste_noire NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE contacts_liste_noire DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0018_cloisonnement_listes"),
        ("contacts", "0004_liste_noire"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]