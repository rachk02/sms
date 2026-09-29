"""Connecteurs vers les passerelles. Voir docs/modele-de-donnees.md, section telecom."""

from django.db import models

from apps.core.models import Base, Identifiable


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


class Correlation(Identifiable):
    """Reprise en base de la correspondance tenue en Redis entre l'identifiant
    rendu par l'opérateur et le message. La base est la vérité, Redis un cache."""

    reference_smsc = models.CharField(max_length=100)
    message_id = models.UUIDField()
    message_date_reception = models.DateTimeField()
    connecteur = models.ForeignKey(Connecteur, on_delete=models.PROTECT, related_name="+")
    cree_le = models.DateTimeField(auto_now_add=True)
    expire_le = models.DateTimeField()

    class Meta:
        db_table = "telecom_correlation"
        constraints = [
            models.UniqueConstraint(
                fields=["connecteur", "reference_smsc"], name="correlation_connecteur_reference"
            )
        ]
        indexes = [models.Index(fields=["expire_le"], name="correlation_expiration")]

    def __str__(self):
        return f"{self.connecteur_id} {self.reference_smsc}"


class AccuseOrphelin(Identifiable):
    """Accusé sans correspondance. Jamais jeté : un flux d'orphelins signale
    presque toujours qu'un opérateur a changé le format de ses identifiants."""

    reference_smsc = models.CharField(max_length=100)
    connecteur = models.ForeignKey(Connecteur, on_delete=models.PROTECT, related_name="+")
    charge_utile = models.JSONField(default=dict)
    recu_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "telecom_accuse_orphelin"

    def __str__(self):
        return f"{self.connecteur_id} {self.reference_smsc}"
