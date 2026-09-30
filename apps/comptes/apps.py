from django.apps import AppConfig


class ComptesConfig(AppConfig):
    name = "apps.comptes"
    label = "comptes"
    verbose_name = "Comptes"

    def ready(self):
        from apps.core import controles

        from .services import controle_bac_a_sable

        controles.enregistrer("bac_a_sable", controle_bac_a_sable)
