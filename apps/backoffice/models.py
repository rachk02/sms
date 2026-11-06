"""Le back-office n'a pas de table à lui. Un modèle mandataire donne une
entrée dans l'administration à un écran qui n'en a pas : le dossier de preuve
d'un numéro (T-75)."""

from apps.contacts.models import Contact


class DossierPreuve(Contact):
    class Meta:
        proxy = True
        verbose_name = "dossier de preuve"
        verbose_name_plural = "dossiers de preuve"
