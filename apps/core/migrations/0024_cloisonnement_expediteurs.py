"""Cloisonnement des identifiants d'expéditeur, de leurs états par réseau et
des noms réservés.

- `expediteurs_expediteur` : cloisonnement ordinaire.
- `expediteurs_reseau` : le client lit ses lignes et les crée à la déclaration
  (une par opérateur, NON_SOUMIS) ; aucune politique ne lui permet de les
  modifier ni de les supprimer. Les décisions des opérateurs sont saisies par le
  back-office, qui contourne ces politiques : aucune route de l'espace client
  ne peut changer un état réseau, et la base le garantit.
- `expediteurs_reserve` : commune à tous, lue par chacun à la déclaration,
  écrite par le back-office seul.
"""

from django.db import migrations

from apps.core.cloisonnement import CLIENT_COURANT, cloisonner

RESEAU = [
    "ALTER TABLE expediteurs_reseau ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE expediteurs_reseau FORCE ROW LEVEL SECURITY",
    "CREATE POLICY expediteurs_reseau_lecture ON expediteurs_reseau FOR SELECT"
    f" USING (client_id = {CLIENT_COURANT})",
    "CREATE POLICY expediteurs_reseau_ajout ON expediteurs_reseau FOR INSERT"
    f" WITH CHECK (client_id = {CLIENT_COURANT} AND etat = 'NON_SOUMIS')",
]
RESEAU_INVERSE = [
    "DROP POLICY expediteurs_reseau_ajout ON expediteurs_reseau",
    "DROP POLICY expediteurs_reseau_lecture ON expediteurs_reseau",
    "ALTER TABLE expediteurs_reseau NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE expediteurs_reseau DISABLE ROW LEVEL SECURITY",
]

RESERVE = [
    "ALTER TABLE expediteurs_reserve ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE expediteurs_reserve FORCE ROW LEVEL SECURITY",
    "CREATE POLICY expediteurs_reserve_lecture ON expediteurs_reserve FOR SELECT USING (true)",
]
RESERVE_INVERSE = [
    "DROP POLICY expediteurs_reserve_lecture ON expediteurs_reserve",
    "ALTER TABLE expediteurs_reserve NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE expediteurs_reserve DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0023_cloisonnement_modeles"),
        ("expediteurs", "0001_initial"),
    ]

    operations = [
        cloisonner("expediteurs_expediteur"),
        migrations.RunSQL(sql=RESEAU, reverse_sql=RESEAU_INVERSE),
        migrations.RunSQL(sql=RESERVE, reverse_sql=RESERVE_INVERSE),
    ]
