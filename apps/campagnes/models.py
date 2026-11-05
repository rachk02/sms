"""Campagnes d'un client. Voir docs/modele-de-donnees.md, section campagnes."""

from django.db import models

from apps.core.models import BaseClient
from apps.expediteurs.models import Expediteur


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


class Campagne(BaseClient):
    """Un envoi à un ensemble de contacts. Composée en brouillon, recalculée à
    chaque aperçu, figée à la confirmation : destinataires, expéditeur par
    réseau et coût annoncés sont ceux qui seront servis et facturés."""

    class Type(models.TextChoices):
        MARKETING = "MARKETING", "Marketing"
        TRANSACTIONNEL = "TRANSACTIONNEL", "Transactionnel"
        OTP = "OTP", "Code à usage unique"

    class Etat(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon"
        PLANIFIEE = "PLANIFIEE", "Planifiée"
        EN_ATTENTE = "EN_ATTENTE", "En attente de diffusion"
        EN_COURS = "EN_COURS", "En cours"
        EN_PAUSE = "EN_PAUSE", "En pause"
        TERMINEE = "TERMINEE", "Terminée"
        ANNULEE = "ANNULEE", "Annulée"

    class Politique(models.TextChoices):
        REFUSER = "REFUSER", "Ne pas envoyer vers ces réseaux"
        REPLI = "REPLI", "Envoyer sous le numéro court"
        ATTENDRE = "ATTENDRE", "Attendre l'approbation"

    nom = models.CharField(max_length=150)
    type = models.CharField(max_length=15, choices=Type, default=Type.MARKETING)
    # Nul : les messages partent sous le numéro court de la plateforme.
    expediteur = models.ForeignKey(
        Expediteur, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    modele_texte = models.TextField()
    # {"listes": ["<uuid>", ...], "segment_id": "<uuid>" ou null}
    cible = models.JSONField(default=dict)
    # Valeurs de repli des champs personnalisés, figées à la confirmation : un
    # champ supprimé ensuite n'empêche pas la diffusion.
    replis = models.JSONField(default=dict, blank=True)
    politique_expediteur = models.CharField(
        max_length=10, choices=Politique, default=Politique.REPLI
    )
    etat = models.CharField(max_length=10, choices=Etat, default=Etat.BROUILLON)
    encodage_prevu = models.CharField(max_length=10, blank=True)
    segments_prevus = models.SmallIntegerField(default=0)
    destinataires_prevus = models.IntegerField(default=0)
    cout_estime = models.IntegerField(default=0)
    cout_reel = models.IntegerField(default=0)
    planifiee_pour = models.DateTimeField(null=True, blank=True)
    # Récurrence (T-65). Une campagne récurrente est un patron : elle ne fige
    # ni ne réserve rien ; chaque occurrence est une campagne fille, figée et
    # réservée à son heure si son coût reste sous le plafond confirmé.
    recurrence_jours = models.SmallIntegerField(null=True, blank=True)
    recurrence_fin = models.DateTimeField(null=True, blank=True)
    plafond_occurrence = models.IntegerField(null=True, blank=True)
    prochaine_occurrence = models.DateTimeField(null=True, blank=True)
    parent = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="occurrences"
    )
    occurrence_le = models.DateTimeField(null=True, blank=True)
    # Lissage (T-66) : la durée choisie par le client ; le nombre de messages
    # par minute qui en découle, fixé à la confirmation ; l'heure du prochain
    # palier de publication, tenue par l'ordonnanceur.
    lissage_duree_minutes = models.IntegerField(null=True, blank=True)
    lissage_par_minute = models.IntegerField(null=True, blank=True)
    prochain_palier = models.DateTimeField(null=True, blank=True)
    confirmee_le = models.DateTimeField(null=True, blank=True)
    demarree_le = models.DateTimeField(null=True, blank=True)
    terminee_le = models.DateTimeField(null=True, blank=True)
    creee_par = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "campagnes_campagne"
        indexes = [models.Index(fields=["client", "etat"], name="campagne_client_etat")]
        constraints = [
            # Une occurrence par créneau : deux ordonnanceurs, ou un rejeu, ne
            # créent jamais deux fois la même.
            models.UniqueConstraint(
                fields=["parent", "occurrence_le"], name="campagne_occurrence_unique"
            ),
        ]

    @property
    def recurrente(self) -> bool:
        return self.recurrence_jours is not None and self.parent_id is None

    def __str__(self):
        return self.nom


class Destinataire(models.Model):
    """Un destinataire figé à la confirmation, avec l'expéditeur sous lequel il
    sera servi. Les contacts refusés par la politique d'expéditeur n'y figurent
    pas. Le texte, lui, se rend à la soumission."""

    class Action(models.TextChoices):
        ENVOYER = "ENVOYER", "Sous l'identifiant"
        REPLI = "REPLI", "Sous le numéro court"
        ATTENDRE = "ATTENDRE", "En attente d'approbation"

    pk = models.CompositePrimaryKey("campagne", "contact_id")
    campagne = models.ForeignKey(Campagne, on_delete=models.CASCADE, related_name="+")
    # Référence au contact (module contacts), sans clé étrangère : un contact
    # supprimé après la confirmation n'empêche pas la campagne de se terminer.
    contact_id = models.UUIDField()
    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    numero_normalise = models.CharField(max_length=20)
    operateur = models.CharField(max_length=20)
    expediteur = models.CharField(max_length=11)
    action = models.CharField(max_length=10, choices=Action)

    class Meta:
        db_table = "campagnes_destinataire"
        indexes = [
            models.Index(fields=["campagne", "operateur"], name="destinataire_campagne_reseau")
        ]

    def __str__(self):
        return self.numero_normalise
