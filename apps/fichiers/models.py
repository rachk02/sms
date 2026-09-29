"""Fichiers déposés dans le stockage objet. Voir docs/modele-de-donnees.md, section fichiers."""

from django.db import models

from apps.core.models import BaseClient


class Fichier(BaseClient):
    class Famille(models.TextChoices):
        PIECE = "PIECE", "Pièce d'onboarding"
        IMPORT = "IMPORT", "Fichier d'import"
        EXPORT = "EXPORT", "Export"
        FACTURE = "FACTURE", "Facture"
        ARCHIVE = "ARCHIVE", "Archive"

    famille = models.CharField(max_length=10, choices=Famille)
    # clients/<client_id>/<famille>/<uuid>.<ext> : jamais le nom d'origine.
    chemin = models.CharField(max_length=255, unique=True)
    nom_origine = models.CharField(max_length=255)
    type_mime = models.CharField(max_length=100)
    taille_octets = models.BigIntegerField()
    chiffre = models.BooleanField(default=False)
    expire_le = models.DateTimeField(null=True, blank=True)
    supprime_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "fichiers_fichier"

    def __str__(self):
        return self.chemin
