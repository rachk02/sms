from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("comptes", "0006_second_facteur"),
        ("journal", "0005_index_soumis"),
    ]

    operations = [
        migrations.AddField(
            model_name="compteur",
            name="latence_cumulee_ms",
            field=models.BigIntegerField(db_default=0, default=0),
        ),
        migrations.AddField(
            model_name="compteur",
            name="latences",
            field=models.IntegerField(db_default=0, default=0),
        ),
        migrations.AddIndex(
            model_name="compteur",
            index=models.Index(fields=["client", "heure"], name="compteur_client_heure"),
        ),
    ]
