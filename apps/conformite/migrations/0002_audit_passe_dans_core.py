"""Le journal d'audit passe dans `core` (décision du 30 septembre 2026).

Seul l'état Django change ici ; la table est renommée par core.0015.
"""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("conformite", "0001_initial"),
        ("core", "0015_audit"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[migrations.DeleteModel(name="Audit")],
        )
    ]
