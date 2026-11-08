from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conformite', '0005_fraude'),
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
    ]
