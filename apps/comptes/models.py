"""Clients et utilisateurs. Voir docs/modele-de-donnees.md, section comptes."""

from django.contrib.auth.base_user import AbstractBaseUser, BaseUserManager
from django.contrib.auth.models import PermissionsMixin
from django.db import models
from django.db.models import Q

from apps.core.models import Base


class Client(Base):
    class Forme(models.TextChoices):
        SOCIETE = "SOCIETE", "Société commerciale"
        ASSOCIATION = "ASSOCIATION", "Association, fondation, ONG"
        PUBLIC = "PUBLIC", "Structure publique"
        DIPLOMATIQUE = "DIPLOMATIQUE", "Représentation diplomatique"
        LIBERALE = "LIBERALE", "Profession libérale"

    class Palier(models.TextChoices):
        DECOUVERTE = "DECOUVERTE", "Découverte"
        VERIFIE = "VERIFIE", "Vérifié"
        CONTRACTUEL = "CONTRACTUEL", "Contractuel"

    class Etat(models.TextChoices):
        ACTIF = "ACTIF", "Actif"
        SUSPENDU = "SUSPENDU", "Suspendu"
        CLOTURE = "CLOTURE", "Clôturé"

    raison_sociale = models.CharField(max_length=200)
    forme = models.CharField(max_length=30, choices=Forme)
    # Nuls plutôt que vides : l'unicité ne porte que sur les valeurs renseignées.
    rccm = models.CharField(max_length=50, null=True, blank=True)  # noqa: DJ001
    ifu = models.CharField(max_length=30, null=True, blank=True)  # noqa: DJ001
    pays = models.CharField(max_length=2, default="BF")
    palier = models.CharField(max_length=20, choices=Palier, default=Palier.DECOUVERTE)
    etat = models.CharField(max_length=20, choices=Etat, default=Etat.ACTIF)
    contact_facturation = models.EmailField(max_length=254, blank=True)
    plafond_horaire = models.IntegerField()
    plafond_journalier = models.IntegerField()
    cloture_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "comptes_client"
        constraints = [
            models.UniqueConstraint(
                fields=["rccm"], condition=Q(rccm__isnull=False), name="client_rccm_unique"
            ),
            models.UniqueConstraint(
                fields=["ifu"], condition=Q(ifu__isnull=False), name="client_ifu_unique"
            ),
        ]

    def __str__(self):
        return self.raison_sociale


class GestionnaireUtilisateur(BaseUserManager):
    use_in_migrations = True

    def _creer(self, courriel, password, **champs):
        if not courriel:
            raise ValueError("Le courriel est obligatoire.")
        utilisateur = self.model(courriel=self.normaliser_courriel(courriel), **champs)
        utilisateur.set_password(password)
        utilisateur.save(using=self._db)
        return utilisateur

    @staticmethod
    def normaliser_courriel(courriel):
        return courriel.strip().lower()

    def get_by_natural_key(self, courriel):
        return self.get(courriel=self.normaliser_courriel(courriel))

    def create_user(self, courriel, password=None, **champs):
        champs.setdefault("is_staff", False)
        champs.setdefault("is_superuser", False)
        return self._creer(courriel, password, **champs)

    def create_superuser(self, courriel, password=None, **champs):
        champs["is_staff"] = True
        champs["is_superuser"] = True
        return self._creer(courriel, password, **champs)


class Utilisateur(AbstractBaseUser, PermissionsMixin, Base):
    """Remplace le modèle utilisateur de Django. Le courriel est l'identifiant de
    connexion. `client` est nul pour les agents EL-IMMEKA, qui n'appartiennent à
    aucun compte client."""

    class Role(models.TextChoices):
        # Deux rôles au lancement (décision du 29 septembre 2026, F-10) :
        # administrateur du compte et utilisateur. Les autres rôles du CDC
        # V3.0 restent déclarés pour la suite et ne sont pas attribués.
        ADMIN = "ADMIN", "Administrateur du compte"
        UTILISATEUR = "UTILISATEUR", "Utilisateur"
        PROPRIETAIRE = "PROPRIETAIRE", "Propriétaire"
        OPERATEUR = "OPERATEUR", "Opérateur de campagne"
        COMPTABLE = "COMPTABLE", "Comptable"
        LECTEUR = "LECTEUR", "Lecteur"

    client = models.ForeignKey(
        Client, on_delete=models.PROTECT, null=True, blank=True, related_name="utilisateurs"
    )
    courriel = models.EmailField(max_length=254, unique=True)
    password = models.CharField(max_length=255, db_column="mot_de_passe")
    last_login = models.DateTimeField(null=True, blank=True, db_column="derniere_connexion_le")
    nom = models.CharField(max_length=150)
    telephone = models.CharField(max_length=20, blank=True)
    role = models.CharField(max_length=30, choices=Role, default=Role.UTILISATEUR)
    courriel_verifie_le = models.DateTimeField(null=True, blank=True)
    second_facteur_actif = models.BooleanField(default=False)
    # Chiffré au repos à partir du lot 3 (T-34), vide jusque-là.
    second_facteur_secret = models.CharField(max_length=255, blank=True)
    echecs_consecutifs = models.SmallIntegerField(default=0)
    verrouille_jusqu_a = models.DateTimeField(null=True, blank=True)
    langue = models.CharField(max_length=2, default="fr")
    is_active = models.BooleanField(default=True, db_column="actif")
    is_staff = models.BooleanField(default=False, db_column="agent_plateforme")
    is_superuser = models.BooleanField(default=False, db_column="super_administrateur")

    objects = GestionnaireUtilisateur()

    USERNAME_FIELD = "courriel"
    EMAIL_FIELD = "courriel"
    REQUIRED_FIELDS = ["nom"]

    class Meta:
        db_table = "comptes_utilisateur"
        indexes = [models.Index(fields=["client", "role"], name="utilisateur_client_role")]

    def __str__(self):
        return self.courriel

    def clean(self):
        super().clean()
        self.courriel = GestionnaireUtilisateur.normaliser_courriel(self.courriel)


class Dossier(Base):
    """Dossier d'onboarding d'un client. Voir la fiche F-11 et T-37 à T-39."""

    class Etat(models.TextChoices):
        BROUILLON = "BROUILLON", "Brouillon"
        DEPOSE = "DEPOSE", "Déposé"
        EN_REVUE = "EN_REVUE", "En revue"
        APPROUVE = "APPROUVE", "Approuvé"
        REJETE = "REJETE", "Rejeté"

    class MotifRejet(models.TextChoices):
        """Liste fermée : sans elle, aucun refus n'est mesurable ni justifiable."""

        PIECE_ILLISIBLE = "PIECE_ILLISIBLE", "Pièce illisible"
        PIECE_EXPIREE = "PIECE_EXPIREE", "Pièce expirée"
        NOM_NON_CONCORDANT = "NOM_NON_CONCORDANT", "Raison sociale non concordante"
        NUMERO_INTROUVABLE = "NUMERO_INTROUVABLE", "Numéro d'existence introuvable ou différent"
        REPRESENTANT_NON_JUSTIFIE = "REPRESENTANT_NON_JUSTIFIE", "Représentant non justifié"
        ACTIVITE_INCOMPATIBLE = "ACTIVITE_INCOMPATIBLE", "Activité incompatible avec l'usage"
        DOUBLON = "DOUBLON", "Doublon avec un compte existant"
        SOUPCON_FAUX = "SOUPCON_FAUX", "Soupçon de faux document"

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="dossiers")
    etat = models.CharField(max_length=20, choices=Etat, default=Etat.BROUILLON)
    depose_le = models.DateTimeField(null=True, blank=True)
    decide_le = models.DateTimeField(null=True, blank=True)
    decide_par = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    motif_rejet = models.CharField(max_length=40, choices=MotifRejet, blank=True)
    motif_detail = models.TextField(blank=True)
    palier_accorde = models.CharField(max_length=20, choices=Client.Palier, blank=True)

    class Meta:
        db_table = "comptes_dossier"
        indexes = [models.Index(fields=["etat", "depose_le"], name="dossier_etat_depot")]

    def __str__(self):
        return f"{self.client} {self.etat}"


class Piece(Base):
    class Type(models.TextChoices):
        RCCM = "RCCM", "Extrait ou attestation RCCM"
        IFU = "IFU", "Attestation IFU"
        PIECE_GERANT = "PIECE_GERANT", "Pièce d'identité du premier responsable"
        DEMANDE_ECRITE = "DEMANDE_ECRITE", "Demande écrite"
        MANDAT = "MANDAT", "Mandat ou procuration"
        RECEPISSE = "RECEPISSE", "Récépissé de déclaration"
        ACTE_CREATION = "ACTE_CREATION", "Acte de création"
        ACCORD_SIEGE = "ACCORD_SIEGE", "Accord de siège, convention ou lettre de mission"
        ATTESTATION_ORDRE = "ATTESTATION_ORDRE", "Attestation d'inscription à l'ordre"

    class Etat(models.TextChoices):
        DEPOSEE = "DEPOSEE", "Déposée"
        ACCEPTEE = "ACCEPTEE", "Acceptée"
        REFUSEE = "REFUSEE", "Refusée"

    client = models.ForeignKey(Client, on_delete=models.PROTECT, related_name="+")
    dossier = models.ForeignKey(Dossier, on_delete=models.PROTECT, related_name="pieces")
    type = models.CharField(max_length=30, choices=Type)
    # fichiers est au-dessus de comptes : référence par UUID simple.
    fichier_id = models.UUIDField()
    expire_le = models.DateField(null=True, blank=True)
    etat = models.CharField(max_length=10, choices=Etat, default=Etat.DEPOSEE)
    motif_refus = models.TextField(blank=True)

    class Meta:
        db_table = "comptes_piece"
        constraints = [
            models.UniqueConstraint(fields=["dossier", "type"], name="piece_une_par_type")
        ]

    def __str__(self):
        return self.get_type_display()


# Pièces attendues selon la forme juridique (fiche F-11). Communes à tous : IFU,
# pièce du premier responsable, demande écrite ; plus la pièce d'existence
# légale propre à la forme. Réclamer un RCCM à une association bloque le
# dossier et fait perdre le client.
PIECES_COMMUNES = (Piece.Type.IFU, Piece.Type.PIECE_GERANT, Piece.Type.DEMANDE_ECRITE)
PIECE_D_EXISTENCE = {
    Client.Forme.SOCIETE: Piece.Type.RCCM,
    Client.Forme.ASSOCIATION: Piece.Type.RECEPISSE,
    Client.Forme.PUBLIC: Piece.Type.ACTE_CREATION,
    Client.Forme.DIPLOMATIQUE: Piece.Type.ACCORD_SIEGE,
    Client.Forme.LIBERALE: Piece.Type.ATTESTATION_ORDRE,
}


def pieces_attendues(forme: str) -> tuple[str, ...]:
    return (PIECE_D_EXISTENCE[forme], *PIECES_COMMUNES)
