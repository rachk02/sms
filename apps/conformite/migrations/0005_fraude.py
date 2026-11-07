import django.db.models.deletion
import uuid
from django.conf import settings
from django.db import migrations, models


# Seuils initiaux (T-100), à confirmer avec l'utilisateur ; modifiables au
# back-office. Le seuil d'alerte des numéros contigus vient du critère de la
# tâche : un envoi vers mille numéros consécutifs déclenche l'alerte. Semés
# ici, avant le cloisonnement de la table (core 0036).
REGLES = (
    {"code": "CONTIGUS", "seuil_alerte": 1000, "seuil_critique": 10000, "fenetre_minutes": 0, "echantillon_min": 0},
    {"code": "CODES_PLAGE", "seuil_alerte": 20, "seuil_critique": 100, "fenetre_minutes": 60, "echantillon_min": 0},
    {"code": "LIVRAISON_PLAGE", "seuil_alerte": 50, "seuil_critique": 20, "fenetre_minutes": 60, "echantillon_min": 50},
)


def semer(apps, schema_editor):
    RegleFraude = apps.get_model("conformite", "RegleFraude")
    RegleFraude.objects.using(schema_editor.connection.alias).bulk_create(
        RegleFraude(**regle) for regle in REGLES
    )


def retirer(apps, schema_editor):
    apps.get_model("conformite", "RegleFraude").objects.using(
        schema_editor.connection.alias
    ).all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ('comptes', '0007_client_mode_reglement'),
        ('conformite', '0004_mots_proscrits'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='RegleFraude',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid7, editable=False, primary_key=True, serialize=False)),
                ('code', models.CharField(choices=[('CONTIGUS', 'Numéros contigus'), ('CODES_PLAGE', 'Codes vers une plage inhabituelle'), ('LIVRAISON_PLAGE', 'Livraison basse sur une plage')], max_length=20, unique=True)),
                ('seuil_alerte', models.PositiveIntegerField()),
                ('seuil_critique', models.PositiveIntegerField()),
                ('fenetre_minutes', models.PositiveIntegerField(default=60)),
                ('echantillon_min', models.PositiveIntegerField(default=0)),
                ('actif', models.BooleanField(default=True)),
                ('modifie_le', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'règle anti-fraude',
                'verbose_name_plural': 'règles anti-fraude',
                'db_table': 'conformite_regle_fraude',
            },
        ),
        migrations.CreateModel(
            name='AlerteFraude',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid7, editable=False, primary_key=True, serialize=False)),
                ('regle', models.CharField(choices=[('CONTIGUS', 'Numéros contigus'), ('CODES_PLAGE', 'Codes vers une plage inhabituelle'), ('LIVRAISON_PLAGE', 'Livraison basse sur une plage')], max_length=20)),
                ('gravite', models.CharField(choices=[('ALERTE', 'Alerte'), ('CRITIQUE', 'Critique')], max_length=10)),
                ('plage', models.CharField(blank=True, max_length=20)),
                ('campagne_id', models.UUIDField(blank=True, null=True)),
                ('valeur', models.IntegerField()),
                ('details', models.JSONField(blank=True, default=dict)),
                ('cree_le', models.DateTimeField(auto_now_add=True)),
                ('modifie_le', models.DateTimeField(auto_now=True)),
                ('traitee_le', models.DateTimeField(blank=True, null=True)),
                ('traitement', models.TextField(blank=True)),
                ('client', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='+', to='comptes.client')),
                ('traitee_par', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, related_name='+', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'alerte anti-fraude',
                'verbose_name_plural': 'alertes anti-fraude',
                'db_table': 'conformite_alerte_fraude',
                'indexes': [models.Index(condition=models.Q(('traitee_le__isnull', True)), fields=['client', 'plage'], name='alerte_fraude_ouverte')],
                'constraints': [models.UniqueConstraint(condition=models.Q(('traitee_le__isnull', True)), fields=('client', 'regle', 'plage', 'campagne_id'), name='alerte_fraude_une_ouverte', nulls_distinct=False)],
            },
        ),
        migrations.RunPython(semer, retirer),
    ]
