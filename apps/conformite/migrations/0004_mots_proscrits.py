import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('conformite', '0003_fenetre_et_jours_feries'),
    ]

    operations = [
        migrations.CreateModel(
            name='MotProscrit',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid7, editable=False, primary_key=True, serialize=False)),
                ('expression', models.CharField(max_length=100)),
                ('forme', models.CharField(editable=False, max_length=100, unique=True)),
                ('gravite', models.CharField(choices=[('AVERTISSEMENT', 'Avertissement'), ('BLOCAGE', 'Blocage')], default='BLOCAGE', max_length=15)),
                ('motif', models.CharField(blank=True, help_text="Montré au client avec l'expression en cause.", max_length=255)),
                ('actif', models.BooleanField(default=True)),
                ('modifie_le', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'mot proscrit',
                'verbose_name_plural': 'mots proscrits',
                'db_table': 'conformite_mot_proscrit',
            },
        ),
    ]
