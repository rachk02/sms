from django.apps import AppConfig


class CampagnesConfig(AppConfig):
    name = "apps.campagnes"
    label = "campagnes"
    verbose_name = "Campagnes"

    def ready(self):
        from apps.core import controles

        from .diffusion import retenue

        controles.enregistrer_retenue("campagne", retenue)
