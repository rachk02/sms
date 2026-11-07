import django.db.models.deletion
import uuid
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('telecom', '0005_mots_cles'),
    ]

    operations = [
        migrations.CreateModel(
            name='IncidentLiaison',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid7, editable=False, primary_key=True, serialize=False)),
                ('etat', models.CharField(choices=[('CONNECTE', 'Connecté'), ('DECONNECTE', 'Déconnecté'), ('DEGRADE', 'Dégradé')], max_length=20)),
                ('motif', models.TextField()),
                ('debut', models.DateTimeField()),
                ('fin', models.DateTimeField(blank=True, null=True)),
                ('mesures', models.JSONField(blank=True, default=dict)),
                ('connecteur', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='telecom.connecteur')),
            ],
            options={
                'verbose_name': 'incident de liaison',
                'verbose_name_plural': 'incidents de liaison',
                'db_table': 'telecom_incident_liaison',
                'indexes': [models.Index(fields=['-debut'], name='incident_liaison_debut')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('fin__isnull', True)), fields=('connecteur',), name='incident_liaison_un_ouvert')],
            },
        ),
    ]
