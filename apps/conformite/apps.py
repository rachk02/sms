from django.apps import AppConfig


class ConformiteConfig(AppConfig):
    name = "apps.conformite"
    label = "conformite"
    verbose_name = "Conformité"

    def ready(self):
        from apps.core import controles

        from .fraude import controle_plage_bloquee

        controles.enregistrer("plage_bloquee", controle_plage_bloquee)
