"""Cloisonnement par ligne des clés d'API (T-106).

La politique ordinaire de cloisonnement, plus une seconde politique, en lecture
seule : une requête de l'API arrive avant que son client soit connu, et doit
retrouver sa clé par le préfixe. Le réglage `app.cle_prefixe` est posé local à
la transaction, le temps de cette lecture, puis vidé ; il ne rend visible que
la ligne dont l'appelant tient déjà le préfixe, jamais une autre, et n'ouvre
aucune écriture. Choix de l'utilisateur du 8 novembre 2026.
"""

from django.db import migrations

from apps.core.cloisonnement import cloisonner

POLITIQUE = "comptes_cle_api_par_prefixe"


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0039_reserve_incidents_publies"),
        ("comptes", "0008_cles_api"),
    ]

    operations = [
        cloisonner("comptes_cle_api"),
        migrations.RunSQL(
            sql=[
                f"CREATE POLICY {POLITIQUE} ON comptes_cle_api FOR SELECT "
                "USING (prefixe = NULLIF(current_setting('app.cle_prefixe', true), ''))"
            ],
            reverse_sql=[f"DROP POLICY {POLITIQUE} ON comptes_cle_api"],
        ),
    ]
