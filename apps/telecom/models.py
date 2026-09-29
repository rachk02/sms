"""Connecteurs vers les passerelles. Voir docs/modele-de-donnees.md, section telecom."""

from django.db import models

from apps.core.models import Base


class Operateur(models.TextChoices):
    ORANGE = "ORANGE", "Orange"
    MOOV = "MOOV", "Moov Africa"
    TELECEL = "TELECEL", "Telecel"


class Connecteur(Base):
    """Une liaison vers une passerelle. Porte les identifiants des opérateurs :
    le rôle applicatif ne lit pas cette table, seuls les workers la voient."""

    class Type(models.TextChoices):
        SMPP = "SMPP", "SMPP par Jasmin"
        HTTP = "HTTP", "HTTP"
        SIMULE = "SIMULE", "Simulateur"

    class EtatLiaison(models.TextChoices):
        CONNECTE = "CONNECTE", "Connecté"
        DECONNECTE = "DECONNECTE", "Déconnecté"
        DEGRADE = "DEGRADE", "Dégradé"

    nom = models.CharField(max_length=100, unique=True)
    operateur = models.CharField(max_length=20, choices=Operateur)
    type = models.CharField(max_length=10, choices=Type)
    hote = models.CharField(max_length=255, blank=True)
    port = models.IntegerField(null=True, blank=True)
    identifiant = models.CharField(max_length=100, blank=True)
    secret_chiffre = models.TextField(blank=True)
    debit_max = models.IntegerField(default=10)
    sessions_max = models.IntegerField(default=1)
    fenetre = models.IntegerField(default=10)
    # Réglages propres à l'adaptateur : délai et taux d'échec du simulateur,
    # options SMPP. Jamais un secret.
    parametres = models.JSONField(default=dict, blank=True)
    actif = models.BooleanField(default=True)
    etat_liaison = models.CharField(
        max_length=20, choices=EtatLiaison, default=EtatLiaison.DECONNECTE
    )
    derniere_connexion_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "telecom_connecteur"

    def __str__(self):
        return self.nom
