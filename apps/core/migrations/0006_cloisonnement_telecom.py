"""telecom_connecteur : aucune lecture ni écriture par le rôle applicatif.

La table porte les identifiants des liaisons SMPP, les secrets les plus
sensibles de la plateforme. Sécurité par ligne activée et forcée, sans aucune
politique : aucun rôle soumis ne voit une ligne. Seuls les workers, qui
contournent la politique, la lisent. Les clés étrangères qui y pointent restent
vérifiées, PostgreSQL faisant ces contrôles sans appliquer la politique.
"""

from django.db import migrations

ACTIVER = [
    "ALTER TABLE telecom_connecteur ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE telecom_connecteur FORCE ROW LEVEL SECURITY",
]

DESACTIVER = [
    "ALTER TABLE telecom_connecteur NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE telecom_connecteur DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0005_cloisonnement_evenements"),
        ("telecom", "0001_initial"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
