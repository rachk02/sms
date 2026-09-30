"""Le journal d'audit passe de `conformite` à `core`.

La table est renommée, pas recréée : aucune entrée n'est perdue. Le
déclencheur d'écriture seule, sa fonction et la politique d'insertion suivent
la table et prennent son nouveau nom.
"""

import uuid

import django.db.models.deletion
from django.db import migrations, models


RENOMMER = [
    "ALTER TABLE conformite_audit RENAME TO core_audit",
    "ALTER TRIGGER conformite_audit_ecriture_seule ON core_audit"
    " RENAME TO core_audit_ecriture_seule",
    "ALTER FUNCTION conformite_audit_ecriture_seule() RENAME TO core_audit_ecriture_seule",
    "ALTER POLICY conformite_audit_ecriture ON core_audit RENAME TO core_audit_ecriture",
]

RETABLIR = [
    "ALTER POLICY core_audit_ecriture ON core_audit RENAME TO conformite_audit_ecriture",
    "ALTER FUNCTION core_audit_ecriture_seule() RENAME TO conformite_audit_ecriture_seule",
    "ALTER TRIGGER core_audit_ecriture_seule ON core_audit"
    " RENAME TO conformite_audit_ecriture_seule",
    "ALTER TABLE core_audit RENAME TO conformite_audit",
]


class Migration(migrations.Migration):
    dependencies = [
        ("core", "0014_acces_connexions"),
        ("conformite", "0001_initial"),
        ("comptes", "0005_journal_connexions"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(sql=RENOMMER, reverse_sql=RETABLIR)],
            state_operations=[
                migrations.CreateModel(
                    name="Audit",
                    fields=[
                        (
                            "id",
                            models.UUIDField(
                                default=uuid.uuid7,
                                editable=False,
                                primary_key=True,
                                serialize=False,
                            ),
                        ),
                        ("action", models.CharField(max_length=100)),
                        ("objet_type", models.CharField(max_length=100)),
                        ("objet_id", models.CharField(max_length=100)),
                        ("valeur_avant", models.JSONField(blank=True, null=True)),
                        ("valeur_apres", models.JSONField(blank=True, null=True)),
                        ("motif", models.TextField(blank=True)),
                        ("adresse_ip", models.GenericIPAddressField(blank=True, null=True)),
                        ("horodatage", models.DateTimeField(auto_now_add=True)),
                        (
                            "auteur",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="comptes.utilisateur",
                            ),
                        ),
                        (
                            "client",
                            models.ForeignKey(
                                blank=True,
                                null=True,
                                on_delete=django.db.models.deletion.PROTECT,
                                related_name="+",
                                to="comptes.client",
                            ),
                        ),
                    ],
                    options={
                        "db_table": "core_audit",
                        "ordering": ["-horodatage"],
                        "indexes": [
                            models.Index(fields=["objet_type", "objet_id"], name="audit_objet"),
                            models.Index(fields=["client", "-horodatage"], name="audit_client_date"),
                            models.Index(fields=["action", "-horodatage"], name="audit_action_date"),
                        ],
                    },
                )
            ],
        )
    ]
