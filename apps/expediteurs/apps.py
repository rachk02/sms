from django.apps import AppConfig


class ExpediteursConfig(AppConfig):
    name = "apps.expediteurs"
    label = "expediteurs"
    verbose_name = "Expéditeurs"

    def ready(self):
        from apps.core import controles

        from .controle import controle_expediteur

        controles.enregistrer("expediteur", controle_expediteur)
