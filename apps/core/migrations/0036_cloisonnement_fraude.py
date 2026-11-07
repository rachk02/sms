"""Anti-fraude : règles lues par tous, alertes cloisonnées par client.

Les règles et leurs seuils sont communs : le portail les lit à la confirmation
d'une campagne, et seul le back-office les écrit. Une alerte porte le client
qu'elle concerne : écrite dans la transaction du client (confirmation d'une
campagne, envoi unitaire) ou par les workers, lue et traitée par le
back-office, qui contourne les politiques.
"""

from django.db import migrations

from apps.core.cloisonnement import cloisonner

REGLES = "conformite_regle_fraude"

ACTIVER = [
    f"ALTER TABLE {REGLES} ENABLE ROW LEVEL SECURITY",
    f"ALTER TABLE {REGLES} FORCE ROW LEVEL SECURITY",
    f"CREATE POLICY {REGLES}_lecture ON {REGLES} FOR SELECT USING (true)",
]

DESACTIVER = [
    f"DROP POLICY {REGLES}_lecture ON {REGLES}",
    f"ALTER TABLE {REGLES} NO FORCE ROW LEVEL SECURITY",
    f"ALTER TABLE {REGLES} DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0035_cloisonnement_mots_proscrits"),
        ("conformite", "0005_fraude"),
    ]

    operations = [
        migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER),
        cloisonner("conformite_alerte_fraude"),
    ]
