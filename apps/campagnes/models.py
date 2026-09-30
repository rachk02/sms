"""Campagnes d'un client. Voir docs/modele-de-donnees.md, section campagnes."""

from django.db import models

from apps.core.models import BaseClient


class Modele(BaseClient):
    """Un texte de message réutilisable, balises comprises. Enregistré
    seulement après analyse : ses balises sont connues et ont leur repli."""

    nom = models.CharField(max_length=150)
    texte = models.TextField()
    # Les noms des balises du texte, dans l'ordre : ["prenom", "code_client"].
    variables = models.JSONField(default=list)

    class Meta:
        db_table = "campagnes_modele"
        constraints = [
            models.UniqueConstraint(fields=["client", "nom"], name="modele_nom_unique"),
        ]

    def __str__(self):
        return self.nom
