from django.db import migrations, models


# Seuils de la règle du plafond du compte (T-101), à confirmer avec
# l'utilisateur : l'alerte part au premier envoi refusé, au-delà de 100 % du
# plafond ; la suspension automatique quand les envois tentés atteignent 200 %
# du plafond sur la même heure ou le même jour.
REGLE = {
    "code": "PLAFOND_COMPTE",
    "seuil_alerte": 100,
    "seuil_critique": 200,
    "fenetre_minutes": 0,
    "echantillon_min": 0,
}


def semer(apps, schema_editor):
    apps.get_model("conformite", "RegleFraude").objects.using(
        schema_editor.connection.alias
    ).create(**REGLE)


def retirer(apps, schema_editor):
    apps.get_model("conformite", "RegleFraude").objects.using(
        schema_editor.connection.alias
    ).filter(code=REGLE["code"]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ('conformite', '0005_fraude'),
        ('core', '0036_cloisonnement_fraude'),
    ]

    operations = [
        migrations.AlterField(
            model_name='alertefraude',
            name='regle',
            field=models.CharField(choices=[('CONTIGUS', 'Numéros contigus'), ('CODES_PLAGE', 'Codes vers une plage inhabituelle'), ('LIVRAISON_PLAGE', 'Livraison basse sur une plage'), ('PLAFOND_COMPTE', 'Plafond du compte dépassé')], max_length=20),
        ),
        migrations.AlterField(
            model_name='reglefraude',
            name='code',
            field=models.CharField(choices=[('CONTIGUS', 'Numéros contigus'), ('CODES_PLAGE', 'Codes vers une plage inhabituelle'), ('LIVRAISON_PLAGE', 'Livraison basse sur une plage'), ('PLAFOND_COMPTE', 'Plafond du compte dépassé')], max_length=20, unique=True),
        ),
        migrations.RunPython(semer, retirer),
    ]
