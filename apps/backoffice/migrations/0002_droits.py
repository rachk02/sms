from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("backoffice", "0001_dossier_de_preuve"),
    ]

    operations = [
        migrations.CreateModel(
            name="Droit",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True, primary_key=True, serialize=False, verbose_name="ID"
                    ),
                ),
            ],
            options={
                "permissions": [
                    ("suspendre_client", "Suspendre et réactiver un compte client"),
                    ("cloturer_client", "Clore un compte client"),
                    ("crediter_client", "Attribuer des crédits à un client"),
                    ("rembourser_client", "Rembourser le solde d'un compte clos"),
                    ("emettre_avoir", "Émettre un avoir"),
                    (
                        "reinitialiser_second_facteur",
                        "Réinitialiser le second facteur d'un utilisateur",
                    ),
                ],
                "managed": False,
                "default_permissions": (),
            },
        ),
    ]
