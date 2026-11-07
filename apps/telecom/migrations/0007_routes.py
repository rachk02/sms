import django.core.validators
import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('telecom', '0006_incidents_liaison'),
    ]

    operations = [
        migrations.CreateModel(
            name='Route',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid7, editable=False, primary_key=True, serialize=False)),
                ('operateur', models.CharField(choices=[('ORANGE', 'Orange'), ('MOOV', 'Moov Africa'), ('TELECEL', 'Telecel')], max_length=20)),
                ('priorite', models.PositiveSmallIntegerField()),
                ('seuil_bascule', models.PositiveSmallIntegerField(default=85, validators=[django.core.validators.MaxValueValidator(100)])),
                ('actif', models.BooleanField(default=True)),
                ('connecteur', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='routes', to='telecom.connecteur')),
            ],
            options={
                'db_table': 'telecom_route',
                'constraints': [models.UniqueConstraint(fields=('operateur', 'priorite'), name='route_priorite_unique'), models.UniqueConstraint(fields=('operateur', 'connecteur'), name='route_connecteur_unique')],
            },
        ),
    ]
