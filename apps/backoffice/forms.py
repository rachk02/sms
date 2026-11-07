from django import forms

from apps.comptes.models import Client, Piece, pieces_attendues
from apps.paiements.services import MANUELS


class EnregistrementClientForm(forms.Form):
    """Saisie de l'agent après signature du contrat. Les règles (pièces selon la
    forme, unicité des numéros) sont dans backoffice.services : le formulaire ne
    fait que recueillir."""

    raison_sociale = forms.CharField(max_length=200)
    forme = forms.ChoiceField(choices=Client.Forme.choices, label="Forme juridique")
    rccm = forms.CharField(max_length=50, required=False, label="Numéro RCCM")
    ifu = forms.CharField(max_length=30, label="Numéro IFU")
    contact_facturation = forms.EmailField(required=False, label="Courriel de facturation")
    representant_nom = forms.CharField(max_length=150, label="Nom du représentant")
    representant_courriel = forms.EmailField(label="Courriel du représentant")
    representant_telephone = forms.CharField(
        max_length=20, required=False, label="Téléphone du représentant"
    )

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for type_piece, libelle in Piece.Type.choices:
            formes = [f.label for f in Client.Forme if type_piece in pieces_attendues(f)]
            aide = (
                "Attendue pour toutes les formes"
                if len(formes) == len(Client.Forme)
                else ("Attendue pour : " + ", ".join(formes) if formes else "Facultative")
            )
            self.fields[f"piece_{type_piece}"] = forms.FileField(
                required=False, label=libelle, help_text=aide
            )


class PaiementManuelForm(forms.Form):
    """Saisie d'un virement ou d'un versement en espèces reçu. Les règles (pas
    d'achat, justificatif, doublon) sont dans paiements.services."""

    client = forms.ModelChoiceField(queryset=Client.objects.order_by("raison_sociale"))
    fournisseur = forms.ChoiceField(
        choices=[(f.value, f.label) for f in MANUELS], label="Moyen de paiement"
    )
    reference = forms.CharField(
        max_length=100, label="Référence", help_text="Référence du virement ou numéro du reçu."
    )
    credits = forms.IntegerField(
        min_value=1, label="Crédits (FCFA hors taxes)", help_text="La TVA s'ajoute au montant payé."
    )
    motif = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))
    justificatif = forms.FileField(
        required=False, help_text="Bordereau de virement ou reçu : PDF, JPEG ou PNG, 5 Mo au plus."
    )


class AvoirForm(forms.Form):
    """Avoir sur une facture : total ou partiel, motif obligatoire."""

    montant_ht = forms.IntegerField(min_value=1, label="Montant de l'avoir (FCFA hors taxes)")
    motif = forms.CharField(widget=forms.Textarea(attrs={"rows": 3}))


class ClotureForm(forms.Form):
    motif = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="La demande écrite du client : date, auteur, moyen de réception.",
    )


class RemboursementForm(forms.Form):
    reference = forms.CharField(
        max_length=100, label="Référence du virement", help_text="Le virement fait au client."
    )


class RolesForm(forms.Form):
    roles = forms.MultipleChoiceField(
        choices=[],
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Rôles",
        help_text="Cumulables. Aucun : l'agent se connecte et ne voit rien.",
    )
    motif = forms.CharField(widget=forms.Textarea(attrs={"rows": 2}))

    def __init__(self, *args, **kwargs):
        from .roles import ROLES

        super().__init__(*args, **kwargs)
        self.fields["roles"].choices = [(role, role) for role in ROLES]
