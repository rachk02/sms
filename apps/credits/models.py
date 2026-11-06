"""Registre de crédits, cache de solde et réservations.

Voir docs/modele-de-donnees.md, section credits, et la charte, « Le registre de
crédits ». Tous les montants sont des FCFA entiers.
"""

from django.contrib.postgres.constraints import ExclusionConstraint
from django.contrib.postgres.fields import DateTimeRangeField, RangeBoundary, RangeOperators
from django.db import models
from django.db.models import Func, Q

from apps.comptes.models import Client
from apps.core.models import Identifiable


class EcritureSeuleError(Exception):
    """Levée par toute tentative de modifier ou de supprimer une écriture."""


class EcrituresQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise EcritureSeuleError("credits_registre est en écriture seule : mise à jour refusée.")

    def delete(self):
        raise EcritureSeuleError("credits_registre est en écriture seule : suppression refusée.")

    def bulk_update(self, objs, fields, batch_size=None):
        raise EcritureSeuleError("credits_registre est en écriture seule : mise à jour refusée.")

    def bulk_create(self, objs, *args, **kwargs):
        # Une insertion qui, en conflit, mettrait à jour la ligne existante est
        # une modification déguisée.
        if kwargs.get("update_conflicts"):
            raise EcritureSeuleError(
                "credits_registre est en écriture seule : mise à jour en conflit refusée."
            )
        return super().bulk_create(objs, *args, **kwargs)


class Ecriture(Identifiable):
    """Une ligne du registre. Aucune ligne n'est modifiée ni supprimée, jamais :
    le modèle le refuse, et un déclencheur PostgreSQL le refuse aussi."""

    class Type(models.TextChoices):
        ACHAT = "ACHAT", "Achat"
        CONSOMMATION = "CONSOMMATION", "Consommation"
        RECREDIT = "RECREDIT", "Recrédit"
        AJUSTEMENT = "AJUSTEMENT", "Ajustement"

    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    type = models.CharField(max_length=20, choices=Type)
    montant = models.IntegerField()
    # Solde acquis après l'écriture. Sert au contrôle, jamais à la lecture du solde.
    solde_apres = models.BigIntegerField()
    reference = models.CharField(max_length=100, blank=True)
    motif = models.TextField(blank=True)
    auteur = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    objects = EcrituresQuerySet.as_manager()

    class Meta:
        db_table = "credits_registre"
        indexes = [models.Index(fields=["client", "cree_le"], name="registre_client_date")]
        constraints = [
            # Un message ne consomme qu'une fois, quelle que soit la relivraison
            # de sa soumission : la base le garantit, pas seulement le service.
            models.UniqueConstraint(
                fields=["reference"],
                condition=Q(type="CONSOMMATION") & Q(reference__startswith="message:"),
                name="registre_consommation_unique_par_message",
            ),
            models.CheckConstraint(
                condition=~Q(type="AJUSTEMENT") | ~Q(motif=""), name="registre_ajustement_motive"
            ),
            # Un achat renvoie toujours à sa transaction de paiement.
            models.CheckConstraint(
                condition=~Q(type="ACHAT") | ~Q(reference=""), name="registre_achat_reference"
            ),
            # Un recrédit est soit automatique, et renvoie au message ou à
            # l'incident, soit manuel, et porte son agent et son motif.
            models.CheckConstraint(
                condition=(
                    ~Q(type="RECREDIT")
                    | ~Q(reference="")
                    | (Q(auteur__isnull=False) & ~Q(motif=""))
                ),
                name="registre_recredit_trace",
            ),
            models.CheckConstraint(
                condition=(
                    Q(type__in=["ACHAT", "RECREDIT"], montant__gt=0)
                    | Q(type="CONSOMMATION", montant__lt=0)
                    | (Q(type="AJUSTEMENT") & ~Q(montant=0))
                ),
                name="registre_signe_du_montant",
            ),
        ]

    def __str__(self):
        return f"{self.type} {self.montant}"

    def save(self, *args, **kwargs):
        if not self._state.adding:
            raise EcritureSeuleError(
                "credits_registre est en écriture seule : mise à jour refusée."
            )
        kwargs["force_insert"] = True
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise EcritureSeuleError("credits_registre est en écriture seule : suppression refusée.")


class CompteCredit(models.Model):
    """Cache du solde d'un client. Verrouillé (SELECT ... FOR UPDATE) pendant une
    réservation. La vérité reste la somme du registre."""

    client = models.OneToOneField(
        "comptes.Client", on_delete=models.PROTECT, primary_key=True, related_name="+"
    )
    solde_acquis = models.BigIntegerField(default=0)
    montant_reserve = models.BigIntegerField(default=0)
    mis_a_jour_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "credits_compte"
        constraints = [
            models.CheckConstraint(
                condition=Q(montant_reserve__gte=0), name="compte_reserve_positif"
            ),
        ]

    def __str__(self):
        return f"{self.client_id} {self.solde_acquis}"

    @property
    def solde_disponible(self) -> int:
        return self.solde_acquis - self.montant_reserve


class Reservation(Identifiable):
    """Retenue provisoire, sur une campagne entière ou sur un envoi unitaire."""

    class Etat(models.TextChoices):
        ACTIVE = "ACTIVE", "Active"
        REGLEE = "REGLEE", "Réglée"
        LIBEREE = "LIBEREE", "Libérée"

    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    campagne_id = models.UUIDField(null=True, blank=True)
    message_id = models.UUIDField(null=True, blank=True)
    message_date_reception = models.DateTimeField(null=True, blank=True)
    montant_initial = models.IntegerField()
    montant_restant = models.IntegerField()
    etat = models.CharField(max_length=10, choices=Etat, default=Etat.ACTIVE)
    # Mode de règlement du client au moment de la réservation, figé : un client
    # passé à l'autre mode en cours de campagne ne change rien à celle-ci.
    mode = models.CharField(
        max_length=12,
        choices=Client.Reglement,
        default=Client.Reglement.SOUMISSION,
        db_default="SOUMISSION",
    )
    cree_le = models.DateTimeField(auto_now_add=True)
    resolue_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "credits_reservation"
        indexes = [models.Index(fields=["client", "etat"], name="reservation_client_etat")]
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(campagne_id__isnull=False, message_id__isnull=True)
                    | Q(campagne_id__isnull=True, message_id__isnull=False)
                ),
                name="reservation_campagne_ou_message",
            ),
            models.CheckConstraint(
                condition=Q(
                    montant_restant__gte=0, montant_restant__lte=models.F("montant_initial")
                ),
                name="reservation_restant_borne",
            ),
            models.UniqueConstraint(
                fields=["campagne_id"],
                condition=Q(campagne_id__isnull=False),
                name="reservation_campagne_unique",
            ),
            models.UniqueConstraint(
                fields=["message_id"],
                condition=Q(message_id__isnull=False),
                name="reservation_message_unique",
            ),
        ]

    def __str__(self):
        return f"{self.etat} {self.montant_restant}/{self.montant_initial}"


# Grilles tarifaires (T-81)


class TsTzRange(Func):
    function = "TSTZRANGE"
    output_field = DateTimeRangeField()


class Grille(Identifiable):
    """Une grille de prix. La grille publique est unique et sans client ; une
    grille négociée appartient à un client, une seule par client ; une
    promotion vise tous les clients (sans client) ou un client nommé, et ses
    versions ont toujours une fin. Les prix vivent dans les versions datées :
    une grille sans version publiée n'est jamais appliquée. Priorité : voir
    apps/credits/tarifs.py."""

    class Nature(models.TextChoices):
        PUBLIQUE = "PUBLIQUE", "Grille publique"
        NEGOCIEE = "NEGOCIEE", "Grille négociée"
        PROMOTION = "PROMOTION", "Promotion"

    nom = models.CharField(max_length=150)
    nature = models.CharField(
        max_length=10, choices=Nature, default=Nature.PUBLIQUE, db_default="PUBLIQUE"
    )
    client = models.ForeignKey(
        "comptes.Client", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    devise = models.CharField(max_length=3, default="XOF")
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "credits_grille"
        constraints = [
            models.CheckConstraint(
                condition=(
                    Q(nature="PUBLIQUE", client__isnull=True)
                    | Q(nature="NEGOCIEE", client__isnull=False)
                    | Q(nature="PROMOTION")
                ),
                name="grille_nature_client",
            ),
            models.UniqueConstraint(
                fields=["nature"],
                condition=Q(nature="PUBLIQUE"),
                name="grille_publique_unique",
            ),
            models.UniqueConstraint(
                fields=["client"],
                condition=Q(nature="NEGOCIEE"),
                name="grille_negociee_unique",
            ),
        ]

    def __str__(self):
        return self.nom


class VersionGrille(Identifiable):
    """Une version datée d'une grille, en vigueur sur [debut, fin), fin nulle pour
    une version sans terme. Brouillon tant qu'elle n'est pas publiée ; une
    version publiée ne change plus, sinon pour recevoir sa fin quand la suivante
    est publiée. Deux versions publiées d'une même grille ne se chevauchent
    jamais : la base le refuse."""

    grille = models.ForeignKey(Grille, on_delete=models.PROTECT, related_name="versions")
    debut = models.DateTimeField(null=True, blank=True)
    fin = models.DateTimeField(null=True, blank=True)
    publiee = models.BooleanField(default=False)
    publiee_le = models.DateTimeField(null=True, blank=True)
    publiee_par = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    cree_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "credits_grille_version"
        constraints = [
            models.CheckConstraint(
                condition=Q(fin__isnull=True)
                | Q(debut__isnull=True)
                | Q(fin__gt=models.F("debut")),
                name="version_fin_apres_debut",
            ),
            models.CheckConstraint(
                condition=Q(publiee=False) | Q(debut__isnull=False, publiee_le__isnull=False),
                name="version_publiee_datee",
            ),
            ExclusionConstraint(
                name="version_sans_chevauchement",
                expressions=[
                    ("grille", RangeOperators.EQUAL),
                    (TsTzRange("debut", "fin", RangeBoundary()), RangeOperators.OVERLAPS),
                ],
                condition=Q(publiee=True),
            ),
        ]

    def __str__(self):
        return f"{self.grille} du {self.debut} au {self.fin or '...'}"


class PalierGrille(Identifiable):
    """Prix d'un segment pour un pays et un réseau, à partir d'un volume
    mensuel. Le palier retenu est celui du plus grand `volume_min` atteint."""

    version = models.ForeignKey(VersionGrille, on_delete=models.PROTECT, related_name="paliers")
    pays = models.CharField(max_length=2)
    operateur = models.CharField(max_length=20)
    volume_min = models.PositiveIntegerField(default=0)
    prix_unitaire = models.PositiveIntegerField()

    class Meta:
        db_table = "credits_grille_palier"
        constraints = [
            models.UniqueConstraint(
                fields=["version", "pays", "operateur", "volume_min"], name="palier_unique"
            ),
            models.CheckConstraint(condition=Q(prix_unitaire__gt=0), name="palier_prix_positif"),
        ]

    def __str__(self):
        return f"{self.pays} {self.operateur} dès {self.volume_min} : {self.prix_unitaire} FCFA"
