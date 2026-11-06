"""Journal des messages. Voir docs/modele-de-donnees.md, section journal.

`journal_message` et `journal_evenement` sont partitionnées par mois : leurs
tables sont créées par SQL dans la migration, Django n'en connaît que la forme.
Leur clé primaire est composée de `id` et de la clé de partitionnement, ce que
PostgreSQL impose. Les références vers les modules de couche supérieure
(campagnes, contacts) sont des UUID simples, sans clé étrangère.
"""

import uuid

from django.db import models

from apps.core.etats import Etat


class Encodage(models.TextChoices):
    GSM7 = "GSM7", "GSM 7 bits"
    UCS2 = "UCS2", "UCS-2"


class Message(models.Model):
    class Nature(models.TextChoices):
        CLIENT = "CLIENT", "Envoi du client"
        # Obligation de la plateforme, envoyée au nom du client : la
        # confirmation d'une désinscription (T-71). Gratuite, hors registre de
        # crédits, et soumise même vers un numéro de la liste noire.
        SYSTEME = "SYSTEME", "Message système"

    pk = models.CompositePrimaryKey("id", "date_reception")
    id = models.UUIDField(default=uuid.uuid7, editable=False)
    client = models.ForeignKey(
        "comptes.Client", on_delete=models.PROTECT, related_name="+", db_index=False
    )
    campagne_id = models.UUIDField(null=True, blank=True)
    contact_id = models.UUIDField(null=True, blank=True)
    numero_normalise = models.CharField(max_length=20)
    operateur = models.CharField(max_length=20)
    expediteur_libelle = models.CharField(max_length=11)
    texte = models.TextField()
    nature = models.CharField(
        max_length=10, choices=Nature, default=Nature.CLIENT, db_default=Nature.CLIENT
    )
    encodage = models.CharField(max_length=10, choices=Encodage)
    segments = models.SmallIntegerField()
    cout_unitaire = models.IntegerField()
    cout_total = models.IntegerField()
    etat = models.CharField(max_length=20, choices=Etat, default=Etat.RECU)
    motif_echec = models.CharField(max_length=100, blank=True)
    connecteur = models.ForeignKey(
        "telecom.Connecteur",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="+",
        db_index=False,
    )
    reference_smsc = models.CharField(max_length=100, blank=True)
    cle_idempotence = models.CharField(max_length=100, blank=True)
    date_reception = models.DateTimeField()
    soumission_debutee_le = models.DateTimeField(null=True, blank=True)
    date_soumission = models.DateTimeField(null=True, blank=True)
    date_accuse = models.DateTimeField(null=True, blank=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "journal_message"
        indexes = [
            models.Index(fields=["client", "-date_reception"], name="message_client_date"),
            models.Index(
                fields=["client", "numero_normalise", "-date_reception"],
                name="message_client_numero_date",
            ),
            models.Index(fields=["campagne_id", "etat"], name="message_campagne_etat"),
            # Constat des messages restés sans accusé : seuls les SOUMIS comptent.
            models.Index(
                fields=["date_soumission"],
                condition=models.Q(etat="SOUMIS"),
                name="message_soumis_date",
            ),
            # Rapprochement d'un STOP reçu sur le numéro court partagé, tous clients confondus.
            models.Index(
                fields=["numero_normalise", "-date_reception"], name="message_numero_date"
            ),
        ]

    def __str__(self):
        return f"{self.numero_normalise} {self.etat}"


class Evenement(models.Model):
    class Source(models.TextChoices):
        API = "API", "API"
        WORKER = "WORKER", "Worker"
        ACCUSE = "ACCUSE", "Accusé"
        ADMIN = "ADMIN", "Administration"

    pk = models.CompositePrimaryKey("id", "horodatage")
    id = models.UUIDField(default=uuid.uuid7, editable=False)
    client = models.ForeignKey(
        "comptes.Client", on_delete=models.PROTECT, related_name="+", db_index=False
    )
    message_id = models.UUIDField()
    etat = models.CharField(max_length=20, choices=Etat)
    horodatage = models.DateTimeField()
    source = models.CharField(max_length=10, choices=Source)
    details = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "journal_evenement"
        indexes = [
            models.Index(fields=["message_id", "horodatage"], name="evenement_message"),
            models.Index(fields=["client", "-horodatage"], name="evenement_client_date"),
        ]

    def __str__(self):
        return f"{self.message_id} {self.etat}"


class Destinataire(models.Model):
    """Garantit qu'une campagne n'envoie jamais deux fois au même contact.

    Table non partitionnée : une contrainte d'unicité sur `journal_message`
    devrait inclure la date, et un doublon tombé dans un autre mois passerait.
    """

    pk = models.CompositePrimaryKey("campagne_id", "contact_id")
    campagne_id = models.UUIDField()
    contact_id = models.UUIDField()
    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    message_id = models.UUIDField()
    message_date_reception = models.DateTimeField()
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "journal_destinataire"

    def __str__(self):
        return f"{self.campagne_id} {self.contact_id}"


class Compteur(models.Model):
    """Agrégats d'une campagne par réseau, expéditeur et heure, écrits dans la
    transaction de chaque changement d'état d'un message (`changer_etat`).
    C'est ce que lisent le suivi en direct et les tableaux de bord, jamais
    `journal_message`. Chaque transition n'arrive qu'une fois par message : les
    compteurs sont exacts, rejeux compris."""

    pk = models.CompositePrimaryKey(
        "client", "campagne_id", "operateur", "expediteur_libelle", "heure"
    )
    client = models.ForeignKey(
        "comptes.Client", on_delete=models.PROTECT, related_name="+", db_index=False
    )
    campagne_id = models.UUIDField()
    operateur = models.CharField(max_length=20)
    expediteur_libelle = models.CharField(max_length=11)
    heure = models.DateTimeField()
    emis = models.IntegerField(default=0)
    livres = models.IntegerField(default=0)
    echecs = models.IntegerField(default=0)
    sans_accuse = models.IntegerField(default=0)
    # Refusés avant le réseau (liste noire, expéditeur, passerelle), sans frais.
    refuses = models.IntegerField(default=0)
    annules = models.IntegerField(default=0)
    # Somme des coûts des messages émis. Le coût facturé, plafonné à la
    # réservation, se lit sur la réservation de la campagne.
    cout = models.BigIntegerField(default=0)

    class Meta:
        db_table = "journal_compteur"

    def __str__(self):
        return f"{self.campagne_id} {self.operateur} {self.heure}"
