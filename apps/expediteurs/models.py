"""Identifiants d'expéditeur. Voir docs/modele-de-donnees.md, section expediteurs."""

import uuid

from django.db import models
from django.db.models.functions import Upper

from apps.core.models import BaseClient

OPERATEURS = ("ORANGE", "MOOV", "TELECEL")


class Expediteur(BaseClient):
    """Un identifiant alphanumérique déclaré par un client. Son état vis-à-vis
    de chaque réseau vit dans `ExpediteurReseau` : un même identifiant peut être
    approuvé chez un opérateur et refusé chez un autre."""

    class Etat(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon"
        SOUMIS = "SOUMIS", "Soumis"
        ACTIF = "ACTIF", "Actif"
        SUSPENDU = "SUSPENDU", "Suspendu"

    libelle = models.CharField(max_length=11)
    etat = models.CharField(max_length=10, choices=Etat, default=Etat.SOUMIS)
    # Pièce de mandat, dans le stockage objet (module fichiers).
    justificatif_id = models.UUIDField(null=True, blank=True)
    par_defaut = models.BooleanField(default=False)
    # Nom réservé (marque, institution, mot générique) : un agent lève la revue
    # avant tout dépôt chez un opérateur. Jamais d'activation automatique.
    revue_manuelle = models.BooleanField(default=False)
    motif_revue = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "expediteurs_expediteur"
        constraints = [
            models.UniqueConstraint(
                "client", Upper("libelle"), name="expediteur_libelle_unique_par_client"
            ),
            models.CheckConstraint(
                condition=models.Q(libelle__regex=r"^[A-Za-z0-9]{1,11}$")
                & models.Q(libelle__regex=r"[A-Za-z]"),
                name="expediteur_libelle_alphabet",
            ),
        ]

    def __str__(self):
        return self.libelle


class ExpediteurReseau(BaseClient):
    """L'état d'un identifiant chez un opérateur. Le client le lit, ne le
    modifie jamais : les décisions sont saisies par un agent du back-office."""

    class Etat(models.TextChoices):
        NON_SOUMIS = "NON_SOUMIS", "Non soumis"
        EN_INSTRUCTION = "EN_INSTRUCTION", "En instruction"
        APPROUVE = "APPROUVE", "Approuvé"
        REFUSE = "REFUSE", "Refusé"
        REVOQUE = "REVOQUE", "Révoqué"

    expediteur = models.ForeignKey(Expediteur, on_delete=models.CASCADE, related_name="reseaux")
    operateur = models.CharField(max_length=20, choices=[(o, o.title()) for o in OPERATEURS])
    etat = models.CharField(max_length=20, choices=Etat, default=Etat.NON_SOUMIS)
    reference_dossier = models.CharField(max_length=100, blank=True)
    depose_le = models.DateTimeField(null=True, blank=True)
    decide_le = models.DateTimeField(null=True, blank=True)
    motif_refus = models.TextField(blank=True)
    expire_le = models.DateTimeField(null=True, blank=True)
    refus_consecutifs = models.IntegerField(default=0)

    class Meta:
        db_table = "expediteurs_reseau"
        constraints = [
            models.UniqueConstraint(
                fields=["expediteur", "operateur"], name="expediteur_reseau_unique"
            ),
        ]

    def __str__(self):
        return f"{self.expediteur} chez {self.operateur}"


class Reserve(models.Model):
    """Nom interdit ou soumis à revue manuelle : marques, opérateurs,
    institutions, mots génériques. Commun à tous les clients, tenu par le
    back-office."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    libelle = models.CharField(max_length=50, unique=True)
    motif = models.CharField(max_length=200)
    actif = models.BooleanField(default=True)

    class Meta:
        db_table = "expediteurs_reserve"

    def __str__(self):
        return self.libelle
