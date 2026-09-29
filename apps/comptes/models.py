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
        PROPRIETAIRE = "PROPRIETAIRE", "Propriétaire"
        ADMIN = "ADMIN", "Administrateur"
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
    role = models.CharField(max_length=30, choices=Role, default=Role.LECTEUR)
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
