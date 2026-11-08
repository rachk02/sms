"""Billetterie de support (T-102).

Un ticket porte le compte, le demandeur, la catégorie choisie par le client
(qui fixe la priorité), l'état parmi sept, le contexte technique de l'objet
d'où il a été ouvert, et son compteur d'engagement de service. Le fil est fait
de messages, publics ou internes ; une note interne n'est jamais montrée au
client.
"""

from django.contrib.postgres.fields import ArrayField
from django.db import models

from apps.core.models import Identifiable


class Ticket(Identifiable):
    class Categorie(models.TextChoices):
        SERVICE_INDISPONIBLE = "SERVICE_INDISPONIBLE", "Service indisponible"
        ACHEMINEMENT = "ACHEMINEMENT", "Dégradation d'acheminement"
        REQUISITION = "REQUISITION", "Réquisition ou demande du régulateur"
        ACCES_BLOQUE = "ACCES_BLOQUE", "Accès bloqué"
        PAIEMENT = "PAIEMENT", "Paiement non crédité"
        EXPEDITEUR = "EXPEDITEUR", "Identifiant d'expéditeur"
        RECLAMATION = "RECLAMATION", "Réclamation d'un destinataire"
        NON_LIVRE = "NON_LIVRE", "Message non livré, cas isolé"
        ONBOARDING = "ONBOARDING", "Dossier d'onboarding"
        INTEGRATION = "INTEGRATION", "Intégration API ou webhooks"
        FACTURATION = "FACTURATION", "Facturation et pièces comptables"
        USAGE = "USAGE", "Usage et demande d'évolution"

    class Priorite(models.TextChoices):
        P1 = "P1", "P1"
        P2 = "P2", "P2"
        P3 = "P3", "P3"
        P4 = "P4", "P4"

    class Etat(models.TextChoices):
        NOUVEAU = "NOUVEAU", "Nouveau"
        PRIS_EN_CHARGE = "PRIS_EN_CHARGE", "Pris en charge"
        ATTENTE_CLIENT = "ATTENTE_CLIENT", "En attente d'information"
        ESCALADE_N2 = "ESCALADE_N2", "Escaladé au niveau 2"
        ESCALADE_RESEAU = "ESCALADE_RESEAU", "Escaladé à l'opérateur"
        RESOLU = "RESOLU", "Résolu"
        CLOS = "CLOS", "Clos"

    class Canal(models.TextChoices):
        FORMULAIRE = "FORMULAIRE", "Formulaire de l'application"
        COURRIEL = "COURRIEL", "Courriel"
        TELEPHONE = "TELEPHONE", "Téléphone"
        INFORMEL = "INFORMEL", "Messagerie ou appel direct"

    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    demandeur = models.ForeignKey(
        "comptes.Utilisateur",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tickets_demandes",
    )
    canal = models.CharField(max_length=12, choices=Canal, default=Canal.FORMULAIRE)
    categorie = models.CharField(max_length=24, choices=Categorie)
    priorite = models.CharField(max_length=2, choices=Priorite)
    etat = models.CharField(max_length=16, choices=Etat, default=Etat.NOUVEAU)
    objet = models.CharField(max_length=200)
    # L'objet technique d'où le ticket a été ouvert, relu côté serveur.
    contexte = models.JSONField(default=dict, blank=True)
    agent = models.ForeignKey(
        "comptes.Utilisateur",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="tickets_suivis",
    )
    precedent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="suites"
    )
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)
    premiere_reponse_le = models.DateTimeField(null=True, blank=True)
    resolu_le = models.DateTimeField(null=True, blank=True)
    clos_le = models.DateTimeField(null=True, blank=True)
    resolution = models.TextField(blank=True)
    # Attente d'information : depuis quand, et préavis de clôture envoyé.
    attente_depuis = models.DateTimeField(null=True, blank=True)
    preavis_le = models.DateTimeField(null=True, blank=True)
    # Escalade à l'opérateur : son dossier et la relance prévue.
    reference_reseau = models.CharField(max_length=100, blank=True)
    relance_le = models.DateTimeField(null=True, blank=True)
    # Compteur d'engagement : secondes consommées, en heures ouvrées ou en
    # continu selon la catégorie, et départ du segment en cours ; nul quand le
    # compteur est arrêté (attente du client, escalade à l'opérateur, résolu).
    engagement_consomme_s = models.PositiveIntegerField(default=0)
    engagement_depuis = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "support_ticket"
        verbose_name = "ticket"
        verbose_name_plural = "tickets"
        indexes = [
            models.Index(fields=["client", "-cree_le"], name="ticket_client_cree"),
            models.Index(fields=["etat", "-cree_le"], name="ticket_etat_cree"),
        ]

    def __str__(self):
        return f"Ticket {self.reference}"

    @property
    def reference(self) -> str:
        """Référence courte à citer : la fin aléatoire de l'identifiant, jamais
        un numéro d'ordre."""
        return self.id.hex[-8:].upper()


class MessageTicket(Identifiable):
    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    ticket = models.ForeignKey(Ticket, on_delete=models.PROTECT, related_name="messages")
    auteur = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Note entre agents, jamais montrée au client.
    interne = models.BooleanField(default=False)
    corps = models.TextField()
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "support_message"
        verbose_name = "message de ticket"
        verbose_name_plural = "messages de ticket"
        ordering = ["cree_le"]


class PieceTicket(Identifiable):
    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    message = models.ForeignKey(MessageTicket, on_delete=models.PROTECT, related_name="pieces")
    fichier = models.ForeignKey("fichiers.Fichier", on_delete=models.PROTECT, related_name="+")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "support_piece"
        verbose_name = "pièce jointe"
        verbose_name_plural = "pièces jointes"


class Incident(Identifiable):
    """Incident déclaré sur la page d'état (T-103). Table de la plateforme, sans
    client : la page est publique. Seul un humain déclare une panne ; le
    passage en dégradé est automatique et ne passe pas par ici."""

    class Composant(models.TextChoices):
        APPLICATION = "APPLICATION", "Application web"
        API = "API", "API publique"
        ORANGE = "ORANGE", "Envoi vers Orange"
        MOOV = "MOOV", "Envoi vers Moov"
        TELECEL = "TELECEL", "Envoi vers Telecel"
        ACCUSES = "ACCUSES", "Remontée des accusés"
        RECHARGEMENT = "RECHARGEMENT", "Rechargement de crédits"

    class Etat(models.TextChoices):
        DEGRADE = "DEGRADE", "Dégradé"
        PANNE = "PANNE", "En panne"
        MAINTENANCE = "MAINTENANCE", "Maintenance planifiée"
        RETABLI = "RETABLI", "Rétabli"

    titre = models.CharField(max_length=200)
    composants = ArrayField(models.CharField(max_length=12, choices=Composant))
    etat = models.CharField(max_length=12, choices=Etat)
    debut_le = models.DateTimeField()
    fin_le = models.DateTimeField(null=True, blank=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "support_incident"
        verbose_name = "incident publié"
        verbose_name_plural = "incidents publiés"
        indexes = [models.Index(fields=["-debut_le"], name="incident_debut")]

    def __str__(self):
        return self.titre


class PublicationIncident(Identifiable):
    incident = models.ForeignKey(Incident, on_delete=models.PROTECT, related_name="publications")
    texte = models.TextField()
    etat = models.CharField(max_length=12, choices=Incident.Etat)
    auteur = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, related_name="+"
    )
    publiee_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "support_publication_incident"
        verbose_name = "publication d'incident"
        verbose_name_plural = "publications d'incident"
        ordering = ["publiee_le"]
