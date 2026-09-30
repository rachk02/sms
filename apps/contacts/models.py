"""Contacts d'un client. Voir docs/modele-de-donnees.md, section contacts."""

import uuid

from django.db import models

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
            models.Index(fields=["client", "operateur_presume"], name="contact_client_operateur")
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
