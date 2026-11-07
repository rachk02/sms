"""telecom_incident_liaison : réservée aux workers et au back-office.

Table de la plateforme, sans `client_id`. Sécurité par ligne activée et forcée,
sans aucune politique, comme telecom_connecteur : le rôle applicatif n'en voit
aucune ligne. La supervision l'écrit sous le rôle worker, le back-office la lit.
"""

from django.db import migrations

ACTIVER = [
    "ALTER TABLE telecom_incident_liaison ENABLE ROW LEVEL SECURITY",
    "ALTER TABLE telecom_incident_liaison FORCE ROW LEVEL SECURITY",
]

DESACTIVER = [
    "ALTER TABLE telecom_incident_liaison NO FORCE ROW LEVEL SECURITY",
    "ALTER TABLE telecom_incident_liaison DISABLE ROW LEVEL SECURITY",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0032_cloisonnement_factures"),
        ("telecom", "0006_incidents_liaison"),
    ]

    operations = [migrations.RunSQL(sql=ACTIVER, reverse_sql=DESACTIVER)]
