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


class IncidentLiaison(Identifiable):
    """Une liaison hors de l'état sain, de sa chute à son rétablissement (T-96).

    Ouvert par la supervision au changement d'état, clos au retour à CONNECTE ;
    un passage de DEGRADE à DECONNECTE clôt l'un et ouvre l'autre. Un seul
    incident ouvert par connecteur. Réservée aux workers et au back-office."""

    connecteur = models.ForeignKey(Connecteur, on_delete=models.PROTECT, related_name="+")
    etat = models.CharField(max_length=20, choices=Connecteur.EtatLiaison)
    motif = models.TextField()
    debut = models.DateTimeField()
    fin = models.DateTimeField(null=True, blank=True)
    # Taux d'accusés positifs, nombre d'accusés et délai moyen au moment de
    # l'ouverture, sur la fenêtre de cinq minutes de la santé.
    mesures = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "telecom_incident_liaison"
        verbose_name = "incident de liaison"
        verbose_name_plural = "incidents de liaison"
        constraints = [
            models.UniqueConstraint(
                fields=["connecteur"],
                condition=models.Q(fin__isnull=True),
                name="incident_liaison_un_ouvert",
            )
        ]
        indexes = [models.Index(fields=["-debut"], name="incident_liaison_debut")]

    def __str__(self):
        return f"{self.connecteur} {self.etat} {self.debut:%Y-%m-%d %H:%M}"


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


class MessageEntrant(Identifiable):
    """SMS reçu sur le numéro court ou un identifiant de la plateforme.

    `client` reste nul tant que le message n'est pas rapproché d'un client
    (T-70). Un rappel rejoué par la passerelle ne crée pas de doublon : unicité
    sur le connecteur et l'identifiant rendu par la passerelle.
    """

    client = models.ForeignKey(
        "comptes.Client", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    connecteur = models.ForeignKey(Connecteur, on_delete=models.PROTECT, related_name="+")
    reference = models.CharField(max_length=100)
    numero_expediteur = models.CharField(max_length=20)
    numero_destination = models.CharField(max_length=20)
    texte = models.TextField()
    mot_cle_reconnu = models.CharField(max_length=30, blank=True)
    recu_le = models.DateTimeField()
    traite_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "telecom_message_entrant"
        constraints = [
            models.UniqueConstraint(
                fields=["connecteur", "reference"], name="entrant_connecteur_reference"
            )
        ]
        indexes = [
            models.Index(fields=["numero_expediteur", "-recu_le"], name="entrant_expediteur_date")
        ]

    def __str__(self):
        return f"{self.numero_expediteur} {self.recu_le:%Y-%m-%d %H:%M}"


class MotCle(Identifiable):
    """Mot-clé reconnu dans un SMS entrant. `client` nul : mot-clé de la
    plateforme, valable sur tous les numéros. Écrit en majuscules, sans accent
    ni signe, tel que `mots_cles.normaliser` le produit. Tenu en base : ajouter
    ou retirer un mot-clé change la reconnaissance sans redéploiement."""

    class Action(models.TextChoices):
        DESINSCRIPTION = "DESINSCRIPTION", "Désinscription"
        AIDE = "AIDE", "Aide"
        CONTACT = "CONTACT", "Contact"

    client = models.ForeignKey(
        "comptes.Client", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    mot_cle = models.CharField(max_length=30)
    action = models.CharField(max_length=15, choices=Action)
    reponse_automatique = models.TextField(blank=True)

    class Meta:
        db_table = "telecom_mot_cle"
        constraints = [
            models.UniqueConstraint(
                fields=["client", "mot_cle"], nulls_distinct=False, name="mot_cle_unique"
            )
        ]

    def __str__(self):
        return f"{self.mot_cle} {self.action}"


class Prefixe(Identifiable):
    """Tranche de numérotation attribuée à un opérateur, sur le numéro national.
    Tenue en base et modifiable sans redéploiement : le plan de numérotation
    évolue. Jamais écrite dans le code."""

    pays = models.CharField(max_length=2, default="BF")
    prefixe = models.CharField(max_length=10)
    operateur = models.CharField(max_length=20, choices=Operateur)
    actif = models.BooleanField(default=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "telecom_prefixe"
        constraints = [
            models.UniqueConstraint(fields=["pays", "prefixe"], name="prefixe_unique_par_pays")
        ]

    def __str__(self):
        return f"{self.pays} {self.prefixe} {self.operateur}"
