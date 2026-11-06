"""Transactions de paiement.

Voir docs/modele-de-donnees.md, section paiements. Montants en FCFA entiers :
`montant` est ce que le client paie (TTC), `credits` ce qui est porté à son
registre (HT). Seul le passage à CONFIRME écrit une ligne ACHAT.
"""

from django.db import models
from django.db.models import Q

from apps.core.models import Identifiable


class Transaction(Identifiable):
    class Fournisseur(models.TextChoices):
        ORANGE_MONEY = "ORANGE_MONEY", "Orange Money"
        MOOV_MONEY = "MOOV_MONEY", "Moov Money"
        CARTE = "CARTE", "Carte bancaire"
        VIREMENT = "VIREMENT", "Virement"
        ESPECES = "ESPECES", "Espèces"
        SIMULE = "SIMULE", "Fournisseur simulé"

    class Etat(models.TextChoices):
        INITIE = "INITIE", "Initié"
        EN_ATTENTE = "EN_ATTENTE", "En attente"
        CONFIRME = "CONFIRME", "Confirmé"
        ECHOUE = "ECHOUE", "Échoué"
        EXPIRE = "EXPIRE", "Expiré"
        REMBOURSE = "REMBOURSE", "Remboursé"

    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    fournisseur = models.CharField(max_length=20, choices=Fournisseur)
    # Référence du fournisseur, vide tant qu'il ne l'a pas rendue.
    reference_transaction = models.CharField(max_length=100, blank=True)
    montant = models.PositiveIntegerField()
    credits = models.PositiveIntegerField()
    etat = models.CharField(max_length=12, choices=Etat, default=Etat.INITIE)
    initie_le = models.DateTimeField(auto_now_add=True)
    confirme_le = models.DateTimeField(null=True, blank=True)
    expire_le = models.DateTimeField(null=True, blank=True)
    justificatif = models.ForeignKey(
        "fichiers.Fichier", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    saisi_par = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    # Paiement manuel : pourquoi l'agent l'a saisi, porté aussi sur l'écriture.
    motif = models.TextField(blank=True)
    # Qui a lancé un rechargement en ligne : il reçoit le courriel de confirmation.
    initie_par = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "paiements_transaction"
        indexes = [models.Index(fields=["etat", "expire_le"], name="transaction_etat_expire")]
        constraints = [
            # Aucune saisie manuelle sans justificatif, agent ni motif (T-87).
            models.CheckConstraint(
                condition=~Q(fournisseur__in=["VIREMENT", "ESPECES"])
                | (Q(justificatif__isnull=False) & Q(saisi_par__isnull=False) & ~Q(motif="")),
                name="transaction_manuelle_justifiee",
            ),
            # Le double crédit d'un rappel réémis bute ici.
            models.UniqueConstraint(
                fields=["fournisseur", "reference_transaction"],
                condition=~Q(reference_transaction=""),
                name="transaction_reference_unique",
            ),
            models.CheckConstraint(condition=Q(credits__gt=0), name="transaction_credits_positifs"),
            models.CheckConstraint(
                condition=Q(montant__gte=models.F("credits")), name="transaction_montant_ttc"
            ),
        ]

    def __str__(self):
        return f"{self.fournisseur} {self.reference_transaction or self.id} {self.etat}"


class Reglage(models.Model):
    """Réglages du rechargement, en une seule ligne, modifiables sans
    redéploiement par le back-office.

    La TVA peut se couper : l'utilisateur n'est pas sûr qu'elle s'applique à ce
    service (6 novembre 2026). Les crédits s'achètent par multiples d'un pas
    qui rend la TVA entière (50 FCFA HT à 18 %). Les bornes d'un rechargement
    restent vides tant que l'utilisateur ne les a pas fixées."""

    id = models.PositiveSmallIntegerField(primary_key=True, default=1)
    tva_active = models.BooleanField(default=True)
    taux_tva = models.PositiveSmallIntegerField(default=18)
    credits_min = models.PositiveIntegerField(null=True, blank=True)
    credits_max = models.PositiveIntegerField(null=True, blank=True)
    # Au-delà, un paiement resté sans réponse du fournisseur passe EXPIRE.
    delai_minutes = models.PositiveSmallIntegerField(default=15)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "paiements_reglage"
        constraints = [
            models.CheckConstraint(condition=Q(id=1), name="reglage_ligne_unique"),
            models.CheckConstraint(condition=Q(taux_tva__lte=100), name="reglage_taux_borne"),
            models.CheckConstraint(
                condition=Q(credits_min__isnull=True)
                | Q(credits_max__isnull=True)
                | Q(credits_max__gte=models.F("credits_min")),
                name="reglage_bornes_ordonnees",
            ),
        ]

    def __str__(self):
        tva = f"TVA {self.taux_tva} %" if self.tva_active else "sans TVA"
        return f"Rechargement, {tva}"


class Facture(Identifiable):
    """Facture d'un achat de crédits, ou avoir qui en corrige une (T-88).

    Une ligne émise ne change plus : tout son contenu, émetteur et client
    compris, est figé dans `mentions` au moment de l'émission, et un
    déclencheur refuse toute modification, sauf de rattacher une fois le PDF
    archivé. Une correction passe par un avoir."""

    class Type(models.TextChoices):
        FACTURE = "FACTURE", "Facture"
        AVOIR = "AVOIR", "Avoir"

    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")
    type = models.CharField(max_length=10, choices=Type, default=Type.FACTURE)
    serie = models.CharField(max_length=4)
    annee = models.PositiveSmallIntegerField()
    sequence = models.PositiveIntegerField()
    numero = models.CharField(max_length=20, unique=True)
    transaction = models.ForeignKey(
        Transaction, on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    origine = models.ForeignKey(
        "self", on_delete=models.PROTECT, null=True, blank=True, related_name="avoirs"
    )
    emise_le = models.DateTimeField()
    montant_ht = models.PositiveIntegerField()
    taux_tva = models.PositiveSmallIntegerField()
    tva = models.PositiveIntegerField()
    montant_ttc = models.PositiveIntegerField()
    mentions = models.JSONField()
    motif = models.TextField(blank=True)
    emise_par = models.ForeignKey(
        "comptes.Utilisateur", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )
    fichier = models.ForeignKey(
        "fichiers.Fichier", on_delete=models.PROTECT, null=True, blank=True, related_name="+"
    )

    class Meta:
        db_table = "paiements_facture"
        indexes = [models.Index(fields=["client", "emise_le"], name="facture_client_date")]
        constraints = [
            models.UniqueConstraint(
                fields=["serie", "annee", "sequence"], name="facture_numero_unique"
            ),
            # Un achat, une facture : la relance de l'émission n'en fait pas deux.
            models.UniqueConstraint(
                fields=["transaction"],
                condition=Q(type="FACTURE"),
                name="facture_unique_par_transaction",
            ),
            models.CheckConstraint(
                condition=Q(montant_ttc=models.F("montant_ht") + models.F("tva")),
                name="facture_ttc_egal_ht_plus_tva",
            ),
            models.CheckConstraint(
                condition=(
                    Q(type="FACTURE") & Q(transaction__isnull=False) & Q(origine__isnull=True)
                )
                | (Q(type="AVOIR") & Q(origine__isnull=False) & ~Q(motif="")),
                name="facture_ou_avoir_rattache",
            ),
            models.CheckConstraint(condition=Q(montant_ht__gt=0), name="facture_montant_positif"),
        ]

    def __str__(self):
        return f"{self.get_type_display()} {self.numero}"


class Numerotation(models.Model):
    """Dernier numéro attribué, par série et par année. La ligne est verrouillée
    dans la transaction même qui écrit la facture : deux émissions simultanées
    s'attendent, et un échec rend son numéro avec lui. Ni trou, ni doublon,
    ce qu'une séquence PostgreSQL ne garantit pas."""

    serie = models.CharField(max_length=4)
    annee = models.PositiveSmallIntegerField()
    dernier = models.PositiveIntegerField(default=0)

    class Meta:
        db_table = "paiements_numerotation"
        constraints = [
            models.UniqueConstraint(fields=["serie", "annee"], name="numerotation_unique")
        ]

    def __str__(self):
        return f"{self.serie}-{self.annee} : {self.dernier}"
