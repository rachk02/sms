"""Contacts d'un client. Voir docs/modele-de-donnees.md, section contacts."""

import uuid

from django.contrib.postgres.indexes import GinIndex, OpClass
from django.db import models
from django.db.models import F
from django.db.models.functions import Upper

from apps.core.models import BaseClient


class Contact(BaseClient):
    class Origine(models.TextChoices):
        IMPORT = "IMPORT", "Import de fichier"
        API = "API", "API"
        SAISIE = "SAISIE", "Saisie"

    # Forme internationale sans séparateur : la seule qui se stocke, se compare
    # et se recherche. Jamais de recherche par motif sur le numéro saisi.
    numero_normalise = models.CharField(max_length=20)
    numero_origine = models.CharField(max_length=50, blank=True)
    # Déduit du préfixe, donc présumé : la portabilité le rend indicatif. Un
    # accusé livré par la route d'un opérateur le confirme.
    operateur_presume = models.CharField(max_length=20, blank=True)
    operateur_confirme = models.CharField(max_length=20, blank=True)
    nom = models.CharField(max_length=150, blank=True)
    prenom = models.CharField(max_length=150, blank=True)
    ville = models.CharField(max_length=100, blank=True)
    champs = models.JSONField(default=dict, blank=True)
    origine = models.CharField(max_length=10, choices=Origine)
    consentement_le = models.DateTimeField(null=True, blank=True)
    consentement_source = models.CharField(max_length=200, blank=True)
    actif = models.BooleanField(default=True)

    class Meta:
        db_table = "contacts_contact"
        constraints = [
            models.UniqueConstraint(
                fields=["client", "numero_normalise"], name="contact_unique_par_client"
            )
        ]
        indexes = [
            models.Index(fields=["client", "operateur_presume"], name="contact_client_operateur"),
            models.Index(fields=["client", "ville"], name="contact_client_ville"),
            models.Index(fields=["client", "cree_le"], name="contact_client_entree"),
            # Critères sur les champs personnalisés, par inclusion (@>).
            GinIndex(fields=["champs"], name="contact_champs", opclasses=["jsonb_path_ops"]),
            # Recherche par début de numéro et de nom (LIKE 'x%') : la collation
            # de la base n'étant pas « C », il faut les classes de motif.
            models.Index(
                fields=["client", "numero_normalise"],
                name="contact_numero_prefixe",
                opclasses=["uuid_ops", "varchar_pattern_ops"],
            ),
            models.Index(
                F("client"),
                OpClass(Upper("nom"), name="text_pattern_ops"),
                name="contact_nom_prefixe",
            ),
        ]

    def __str__(self):
        return self.numero_normalise

    @property
    def operateur(self) -> str:
        return self.operateur_confirme or self.operateur_presume


class Import(BaseClient):
    """Un fichier de contacts et son traitement par le worker d'import."""

    class Etat(models.TextChoices):
        DEPOSE = "DEPOSE", "Déposé, mappage à confirmer"
        EN_FILE = "EN_FILE", "En attente de traitement"
        EN_COURS = "EN_COURS", "En cours"
        TERMINE = "TERMINE", "Terminé"
        ECHOUE = "ECHOUE", "Échoué"

    # Référence au fichier déposé dans le stockage objet (module fichiers).
    fichier_id = models.UUIDField()
    nom_origine = models.CharField(max_length=255)
    etat = models.CharField(max_length=10, choices=Etat, default=Etat.DEPOSE)
    # {"entetes": bool, "colonnes": {"numero": 0, "nom": 1, ...}}
    mappage = models.JSONField(default=dict, blank=True)
    lignes_total = models.IntegerField(default=0)
    lignes_acceptees = models.IntegerField(default=0)
    lignes_rejetees = models.IntegerField(default=0)
    doublons = models.IntegerField(default=0)
    erreur = models.TextField(blank=True)
    demarre_le = models.DateTimeField(null=True, blank=True)
    termine_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "contacts_import"

    def __str__(self):
        return self.nom_origine


class ImportRejet(models.Model):
    """Ligne d'un import refusée, avec son motif en clair."""

    id = models.BigAutoField(primary_key=True)
    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    import_lie = models.ForeignKey(
        Import, on_delete=models.CASCADE, related_name="rejets", db_column="import_id"
    )
    numero_ligne = models.IntegerField()
    contenu_ligne = models.TextField()
    motif = models.CharField(max_length=200)

    class Meta:
        db_table = "contacts_import_rejet"
        indexes = [models.Index(fields=["import_lie", "numero_ligne"], name="rejet_import_ligne")]

    def __str__(self):
        return f"ligne {self.numero_ligne} : {self.motif}"


class ImportTransit(models.Model):
    """Table de transit : les lignes valides y sont écrites par paquets, puis
    versées d'un seul coup dans le répertoire. Vidée après chaque import."""

    id = models.BigAutoField(primary_key=True)
    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    import_lie = models.ForeignKey(
        Import, on_delete=models.CASCADE, related_name="+", db_column="import_id"
    )
    numero_ligne = models.IntegerField()
    numero_normalise = models.CharField(max_length=20)
    numero_origine = models.CharField(max_length=50)
    operateur_presume = models.CharField(max_length=20, blank=True)
    nom = models.CharField(max_length=150, blank=True)
    prenom = models.CharField(max_length=150, blank=True)
    ville = models.CharField(max_length=100, blank=True)
    champs = models.JSONField(default=dict, blank=True)

    class Meta:
        db_table = "contacts_import_transit"
        indexes = [models.Index(fields=["import_lie"], name="transit_import")]

    def __str__(self):
        return f"ligne {self.numero_ligne} : {self.numero_normalise}"


class Liste(BaseClient):
    nom = models.CharField(max_length=150)
    description = models.TextField(blank=True)
    # Compteur dénormalisé, tenu dans la transaction de chaque ajout ou retrait :
    # l'affichage d'une liste ne compte jamais ses membres à la volée.
    nombre_contacts = models.IntegerField(default=0)

    class Meta:
        db_table = "contacts_liste"
        constraints = [models.UniqueConstraint(fields=["client", "nom"], name="liste_nom_unique")]

    def __str__(self):
        return self.nom


class ListeContact(models.Model):
    pk = models.CompositePrimaryKey("liste_id", "contact_id")
    liste = models.ForeignKey(Liste, on_delete=models.CASCADE, related_name="+")
    contact = models.ForeignKey(Contact, on_delete=models.CASCADE, related_name="+")
    client = models.ForeignKey(
        "comptes.Client", on_delete=models.PROTECT, related_name="+", db_index=False
    )
    ajoute_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "contacts_liste_contact"

    def __str__(self):
        return f"{self.liste_id} {self.contact_id}"


class ListeNoire(models.Model):
    """Numéro qui ne reçoit plus rien. `client` nul : liste noire de la
    plateforme, qui vaut pour tous les expéditeurs (décision F-16)."""

    class Origine(models.TextChoices):
        STOP_SMS = "STOP_SMS", "Désinscription par SMS"
        MANUEL = "MANUEL", "Ajout manuel"
        IMPORT = "IMPORT", "Import"
        API = "API", "API"
        RECLAMATION = "RECLAMATION", "Réclamation"

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)
    client = models.ForeignKey(
        "comptes.Client", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    numero_normalise = models.CharField(max_length=20)
    origine = models.CharField(max_length=15, choices=Origine)
    # Le message d'origine d'un STOP est conservé mot pour mot : il fait preuve.
    message_origine = models.TextField(blank=True)
    motif = models.TextField(blank=True)
    ajoute_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "contacts_liste_noire"
        constraints = [
            models.UniqueConstraint(
                fields=["client", "numero_normalise"],
                nulls_distinct=False,
                name="liste_noire_unique",
            )
        ]
        indexes = [models.Index(fields=["numero_normalise"], name="liste_noire_numero")]

    def __str__(self):
        return self.numero_normalise


class ChampPersonnalise(BaseClient):
    """Champ déclaré par le client, en plus du nom, du prénom et de la ville.
    Sa valeur de repli, obligatoire, remplace une valeur absente dans un
    message : jamais de « Bonjour , » adressé à personne."""

    class Type(models.TextChoices):
        TEXTE = "TEXTE", "Texte"
        NOMBRE = "NOMBRE", "Nombre"
        DATE = "DATE", "Date"

    MAXIMUM_PAR_CLIENT = 10

    nom = models.SlugField(max_length=50)
    libelle = models.CharField(max_length=100)
    type = models.CharField(max_length=10, choices=Type, default=Type.TEXTE)
    valeur_repli = models.CharField(max_length=200)

    class Meta:
        db_table = "contacts_champ"
        constraints = [
            models.UniqueConstraint(fields=["client", "nom"], name="champ_nom_unique"),
            models.CheckConstraint(
                condition=~models.Q(valeur_repli=""), name="champ_valeur_repli_obligatoire"
            ),
        ]

    def __str__(self):
        return self.libelle


class Segment(BaseClient):
    """Contacts définis par critères. Recalculé à l'aperçu, figé à la
    confirmation d'une campagne (décision F-15)."""

    nom = models.CharField(max_length=150)
    # [{"champ": "ville", "operation": "egal", "valeur": "Bobo"}, ...]
    criteres = models.JSONField(default=list)
    nombre_estime = models.IntegerField(default=0)
    calcule_le = models.DateTimeField(null=True, blank=True)
    actif = models.BooleanField(default=True)
    motif_desactivation = models.CharField(max_length=200, blank=True)

    class Meta:
        db_table = "contacts_segment"

    def __str__(self):
        return self.nom


class Export(BaseClient):
    """Une demande d'export de contacts, produite par le worker. Le lien signé
    part par courriel au demandeur, dont l'adresse est gardée ici."""

    class Etat(models.TextChoices):
        EN_FILE = "EN_FILE", "En attente de traitement"
        EN_COURS = "EN_COURS", "En cours"
        TERMINE = "TERMINE", "Terminé"
        ECHOUE = "ECHOUE", "Échoué"

    courriel = models.EmailField()
    # Les filtres de la recherche, tels que saisis (voir contacts.recherche.Filtres).
    filtres = models.JSONField(default=dict)
    etat = models.CharField(max_length=10, choices=Etat, default=Etat.EN_FILE)
    lignes = models.IntegerField(default=0)
    # Référence au fichier produit dans le stockage objet (module fichiers).
    fichier_id = models.UUIDField(null=True, blank=True)
    erreur = models.TextField(blank=True)
    termine_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "contacts_export"

    def __str__(self):
        return f"Export {self.id}"
