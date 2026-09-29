"""journal_message.connecteur_id devient une clé étrangère vers telecom_connecteur.

La colonne existe déjà : seul l'état Django change, et la base reçoit la
contrainte. PostgreSQL la pose sur la table mère et sur chaque partition.
"""

import django.db.models.deletion
from django.db import migrations, models

AJOUTER = """
ALTER TABLE journal_message
    ADD CONSTRAINT journal_message_connecteur_fk FOREIGN KEY (connecteur_id)
    REFERENCES telecom_connecteur (id) DEFERRABLE INITIALLY DEFERRED
"""

RETIRER = "ALTER TABLE journal_message DROP CONSTRAINT journal_message_connecteur_fk"


class Migration(migrations.Migration):
    dependencies = [
        ("journal", "0001_initial"),
        ("telecom", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            database_operations=[migrations.RunSQL(AJOUTER, reverse_sql=RETIRER)],
            state_operations=[
                migrations.RemoveField(model_name="message", name="connecteur_id"),
                migrations.AddField(
                    model_name="message",
                    name="connecteur",
                    field=models.ForeignKey(
                        blank=True,
                        db_index=False,
                        null=True,
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="+",
                        to="telecom.connecteur",
                    ),
                ),
            ],
        ),
    ]
