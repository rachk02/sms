from django.apps import AppConfig


class ContactsConfig(AppConfig):
    name = "apps.contacts"
    label = "contacts"
    verbose_name = "Contacts"

    def ready(self):
        from apps.core import controles

        from .liste_noire import controle_liste_noire

        controles.enregistrer("liste_noire", controle_liste_noire)
