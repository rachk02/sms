"""Le back-office n'a pas de table à lui. Un modèle mandataire donne une
entrée dans l'administration à un écran qui n'en a pas : le dossier de preuve
d'un numéro (T-75). Un modèle sans table porte les droits des actions sensibles
qui ne sont pas l'écriture d'une table (T-95)."""

from django.db import models

from apps.contacts.models import Contact


class Droit(models.Model):
    """Sans table : seulement les permissions qu'on donne aux rôles d'agent."""

    class Meta:
        managed = False
        default_permissions = ()
        permissions = [
            ("suspendre_client", "Suspendre et réactiver un compte client"),
            ("cloturer_client", "Clore un compte client"),
            ("crediter_client", "Attribuer des crédits à un client"),
            ("rembourser_client", "Rembourser le solde d'un compte clos"),
            ("emettre_avoir", "Émettre un avoir"),
            ("reinitialiser_second_facteur", "Réinitialiser le second facteur d'un utilisateur"),
        ]

    def __str__(self):
        return "droit d'agent"


class DossierPreuve(Contact):
    class Meta:
        proxy = True
        verbose_name = "dossier de preuve"
        verbose_name_plural = "dossiers de preuve"
