"""telecom_route : réservée aux workers et au back-office.

Table de la plateforme, sans `client_id`. Sécurité par ligne activée et forcée,
sans aucune politique, comme telecom_connecteur : le worker d'envoi la lit pour
choisir le connecteur, le back-office l'écrit.
"""

from django.db import migrations

ACTIVER = [
    "ALTER TABLE telecom_route ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE telecom_route FORCE ROW LEVEL SECURITY",
]

DESACTIVER = [
    "ALTER TABLE telecom_route NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE telecom_route DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0033_cloisonnement_incidents_liaison"),
        ("telecom", "0007_routes"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
