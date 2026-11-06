"""Factures : un client ne voit que les siennes. La numérotation est réservée
aux workers et au back-office, qui émettent : sécurité par ligne forcée, sans
politique, le rôle applicatif n'y voit rien."""

from django.db import migrations

from apps.core.cloisonnement import cloisonner

NUMEROTATION = "paiements_numerotation"


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0031_cloisonnement_reglage_paiements"),
        ("paiements", "0004_factures"),
    ]

    operations = [
        cloisonner("paiements_facture"),
        migrations.RunSQL(
            sql=[
                f"ALTER TABLE {NUMEROTATION} ENABLE ROW LEVEL SECURITY",
                f"ALTER TABLE {NUMEROTATION} FORCE ROW LEVEL SECURITY",
            ],
            reverse_sql=[
                f"ALTER TABLE {NUMEROTATION} NO FORCE ROW LEVEL SECURITY",
                f"ALTER TABLE {NUMEROTATION} DISABLE ROW LEVEL SECURITY",
            ],
        ),
    ]
