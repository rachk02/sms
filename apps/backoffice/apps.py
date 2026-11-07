from django.apps import AppConfig
from django.db.models.signals import post_migrate


class BackofficeConfig(AppConfig):
    name = "apps.backoffice"
    label = "backoffice"
    verbose_name = "Back-office"

    def ready(self):
        from .roles import apres_migration

        # Le back-office est la dernière application installée : quand son
        # signal part, les permissions de toutes les autres existent déjà.
        post_migrate.connect(apres_migration, sender=self, dispatch_uid="backoffice-roles")
