"""Tables du module api. Voir docs/modele-de-donnees.md, section api."""

from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.core.models import Cloisonne, Identifiable


class Idempotence(Identifiable, Cloisonne):
    """La réponse produite pour un en-tête `Idempotency-Key`, gardée vingt-quatre
    heures (T-108).

    La ligne est réservée au début de la requête, dans sa transaction, par une
    insertion qui ignore le conflit : un rejeu concurrent attend sur l'index
    unique que la première requête soit validée, puis lit sa réponse. Une
    requête qui échoue emporte sa réservation avec elle."""

    cle = models.CharField(max_length=255)
    # SHA-256 de la méthode, du chemin et du corps de la requête.
    empreinte = models.CharField(max_length=64)
    statut_http = models.SmallIntegerField(null=True, blank=True)
    reponse = models.JSONField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    expire_le = models.DateTimeField()

    class Meta:
        db_table = "api_idempotence"
        constraints = [
            models.UniqueConstraint(fields=["client", "cle"], name="idempotence_unique_par_client")
        ]
        indexes = [models.Index(fields=["expire_le"], name="idempotence_expiration")]

    def __str__(self):
        return self.cle


class PointReception(Identifiable, Cloisonne):
    """Adresse où le client reçoit ses webhooks (T-111).

    Le secret de signature est chiffré au repos par la clé applicative et
    montré une fois, à la création. Cinq échecs de livraison consécutifs
    désactivent le point et préviennent son propriétaire."""

    class Evenement(models.TextChoices):
        LIVRE = "client.message.livre", "Message livré"
        ECHEC = "client.message.echec", "Message en échec définitif"
        ENTRANT = "client.message.entrant", "Message entrant reçu"
        SOLDE_BAS = "client.solde.bas", "Solde sous le seuil d'alerte"

    url = models.URLField(max_length=500)
    secret_chiffre = models.TextField()
    evenements = ArrayField(models.CharField(max_length=40, choices=Evenement.choices))
    actif = models.BooleanField(default=True)
    echecs_consecutifs = models.SmallIntegerField(default=0)
    desactive_le = models.DateTimeField(null=True, blank=True)
    cree_par = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "api_point_reception"

    def __str__(self):
        return self.url


class Livraison(Identifiable, Cloisonne):
    """Une livraison d'un événement à un point de réception, avec ses
    tentatives : le journal que le client consulte lui-même."""

    class Etat(models.TextChoices):
        EN_COURS = "EN_COURS", "En cours"
        LIVREE = "LIVREE", "Livrée"
        # Toutes les tentatives échouées : la livraison est partie au rebut.
        REBUT = "REBUT", "Abandonnée"

    point = models.ForeignKey(PointReception, on_delete=models.CASCADE, related_name="livraisons")
    evenement = models.CharField(max_length=40)
    # La clé de l'événement interne : une livraison par événement et par point.
    cle_evenement = models.CharField(max_length=150)
    corps = models.JSONField()
    etat = models.CharField(max_length=10, choices=Etat.choices, default=Etat.EN_COURS)
    tentatives = models.SmallIntegerField(default=0)
    dernier_statut_http = models.SmallIntegerField(null=True, blank=True)
    derniere_erreur = models.CharField(max_length=200, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    derniere_tentative_le = models.DateTimeField(null=True, blank=True)
    livree_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "api_livraison"
        constraints = [
            models.UniqueConstraint(
                fields=["point", "cle_evenement"], name="livraison_unique_par_point"
            )
        ]
        indexes = [models.Index(fields=["client", "-cree_le"], name="livraison_client_date")]

    def __str__(self):
        return f"{self.evenement} vers {self.point_id}"
