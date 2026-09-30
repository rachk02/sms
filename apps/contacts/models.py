"""Contacts d'un client. Voir docs/modele-de-donnees.md, section contacts."""

from django.db import models

from apps.core.models import BaseClient


class Contact(BaseClient):
    class Origine(models.TextChoices):
        IMPORT = "IMPORT", "Import de fichier"
        API = "API", "API"
        SAISIE = "SAISIE", "Saisie"

    # Forme internationale sans séparateur : la seule qui se stocke, se compare
    # et se recherche. Jamais de recherche par motif sur le numéro saisi.
    numero_normalise = models.CharField(max_length=20)
    numero_origine = models.CharField(max_length=50, blank=True)
    # Déduit du préfixe, donc présumé : la portabilité le rend indicatif. Un
    # accusé livré par la route d'un opérateur le confirme.
    operateur_presume = models.CharField(max_length=20, blank=True)
    operateur_confirme = models.CharField(max_length=20, blank=True)
    nom = models.CharField(max_length=150, blank=True)
    prenom = models.CharField(max_length=150, blank=True)
    ville = models.CharField(max_length=100, blank=True)
    champs = models.JSONField(default=dict, blank=True)
    origine = models.CharField(max_length=10, choices=Origine)
    consentement_le = models.DateTimeField(null=True, blank=True)
    consentement_source = models.CharField(max_length=200, blank=True)
    actif = models.BooleanField(default=True)

    class Meta:
        db_table = "contacts_contact"
        constraints = [
            models.UniqueConstraint(
                fields=["client", "numero_normalise"], name="contact_unique_par_client"
            )
        ]
        indexes = [
            models.Index(fields=["client", "operateur_presume"], name="contact_client_operateur")
        ]

    def __str__(self):
        return self.numero_normalise

    @property
    def operateur(self) -> str:
        return self.operateur_confirme or self.operateur_presume
