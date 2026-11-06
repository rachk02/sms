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

    class Meta:
        db_table = "paiements_transaction"
        indexes = [models.Index(fields=["etat", "expire_le"], name="transaction_etat_expire")]
        constraints = [
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
