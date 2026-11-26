"""Journal d'audit : auteur et client en UUID simples (décision B3 du
9 novembre 2026).

Les colonnes `auteur_id` et `client_id` gardent leur nom, leur type et leur
index ; seules les deux clés étrangères tombent. Aucune politique de
cloisonnement n'est touchée : celle de `core_audit` est une politique
d'insertion seule, indépendante de ces colonnes.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_cloisonnement_essais"),
    ]

    operations = [
        migrations.RemoveIndex("audit", "audit_client_date"),
        # D'abord le type, sous l'ancien nom et avec la colonne nommée à part :
        # la colonne ne change jamais de nom, seules les clés étrangères tombent.
        migrations.AlterField(
            "audit",
            "auteur",
            models.UUIDField(blank=True, db_column="auteur_id", db_index=True, null=True),
        ),
        migrations.AlterField(
            "audit",
            "client",
            models.UUIDField(blank=True, db_column="client_id", db_index=True, null=True),
        ),
        migrations.RenameField("audit", "auteur", "auteur_id"),
        migrations.RenameField("audit", "client", "client_id"),
        migrations.AlterField(
            "audit",
            "auteur_id",
            models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AlterField(
            "audit",
            "client_id",
            models.UUIDField(blank=True, db_index=True, null=True),
        ),
        migrations.AddIndex(
            "audit",
            models.Index(fields=["client_id", "-horodatage"], name="audit_client_date"),
        ),
    ]
