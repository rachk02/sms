from django import forms

from apps.comptes.models import Client, Piece, pieces_attendues
from apps.credits.models import Grille
from apps.paiements.services import MANUELS
from apps.support.models import Ticket
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.services import ConfigurationRefusee, verifier_parametres


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


class SuspensionForm(forms.Form):
    motif = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Ce qui justifie la décision : il figurera au journal d'audit.",
    )


class CreditForm(forms.Form):
    montant = forms.IntegerField(min_value=1, label="Crédits offerts (FCFA)")
    motif = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Geste commercial, réparation d'un incident : il figurera au registre et à "
        "l'audit.",
    )


class ConnecteurForm(forms.ModelForm):
    """Le secret se saisit, ne se relit jamais : champ vide à chaque
    affichage, et vide à l'envoi, il garde le secret en place."""

    secret = forms.CharField(
        required=False,
        strip=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        help_text="Mot de passe de la liaison. Laisser vide pour garder celui qui est enregistré.",
    )

    class Meta:
        model = Connecteur
        fields = (
            "nom",
            "operateur",
            "type",
            "hote",
            "port",
            "identifiant",
            "secret",
            "debit_max",
            "sessions_max",
            "fenetre",
            "parametres",
            "actif",
        )

    def clean_parametres(self):
        parametres = self.cleaned_data.get("parametres") or {}
        try:
            verifier_parametres(parametres)
        except ConfigurationRefusee as refus:
            raise forms.ValidationError(str(refus)) from None
        return parametres


def _date_et_heure(**options):
    return forms.DateTimeField(
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"),
        input_formats=["%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M"],
        **options,
    )


class GrilleForm(forms.Form):
    nom = forms.CharField(max_length=150)
    nature = forms.ChoiceField(choices=Grille.Nature.choices)
    client = forms.ModelChoiceField(
        queryset=Client.objects.order_by("raison_sociale"),
        required=False,
        help_text="Obligatoire pour une grille négociée ; une promotion peut viser un client.",
    )


class VersionForm(forms.Form):
    debut = _date_et_heure(label="Prise d'effet", help_text="Heure de Ouagadougou.")
    fin = _date_et_heure(label="Fin", required=False, help_text="Obligatoire pour une promotion.")
    copier = forms.BooleanField(
        required=False, initial=True, label="Reprendre les prix de la version en vigueur"
    )


class PalierForm(forms.Form):
    pays = forms.CharField(max_length=2, initial="BF")
    operateur = forms.ChoiceField(choices=Operateur.choices, label="Réseau")
    volume_min = forms.IntegerField(min_value=0, initial=0, label="À partir de (segments par mois)")
    prix_unitaire = forms.IntegerField(min_value=1, label="Prix d'un segment (FCFA)")


class ConfirmationForm(forms.Form):
    pass


class TraitementForm(forms.Form):
    traitement = forms.CharField(
        widget=forms.Textarea(attrs={"rows": 3}),
        label="Constat",
        help_text="Ce qui a été vérifié et décidé : il figurera au journal d'audit. Une alerte "
        "critique traitée débloque la plage.",
    )


# Billetterie (T-102)


class TicketReponseForm(forms.Form):
    corps = forms.CharField(label="Message", widget=forms.Textarea(attrs={"rows": 5}))
    interne = forms.BooleanField(
        required=False, label="Note interne", help_text="Jamais montrée au client."
    )
    attendre_client = forms.BooleanField(
        required=False,
        label="Attendre la réponse du client",
        help_text="Met le ticket en attente d'information : le compteur s'arrête, le préavis "
        "part au cinquième jour et le ticket se clôt au septième sans réponse.",
    )


class TicketEtatForm(forms.Form):
    etat = forms.ChoiceField(label="Nouvel état")
    reference_reseau = forms.CharField(
        required=False,
        max_length=100,
        label="Dossier chez l'opérateur",
        help_text="Exigé pour une escalade à l'opérateur.",
    )
    relance_le = forms.DateField(
        required=False,
        label="Relance prévue le",
        widget=forms.DateInput(attrs={"type": "date"}),
        help_text="Exigée pour une escalade à l'opérateur.",
    )
    resolution = forms.CharField(
        required=False,
        label="Résolution retenue",
        widget=forms.Textarea(attrs={"rows": 3}),
        help_text="Exigée pour résoudre : elle est envoyée au client.",
    )

    def __init__(self, *args, choix=(), **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["etat"].choices = list(choix)


class TicketCategorieForm(forms.Form):
    categorie = forms.ChoiceField(label="Catégorie", help_text="Elle refixe la priorité.")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["categorie"].choices = Ticket.Categorie.choices


class SaisieTicketForm(forms.Form):
    """Ressaisie en ticket d'une demande arrivée par un autre canal, avant d'y
    répondre : ce qui n'est pas dans l'outil n'existe pas."""

    client = forms.ModelChoiceField(queryset=Client.objects.order_by("raison_sociale"))
    demandeur = forms.EmailField(
        required=False,
        label="Courriel du demandeur",
        help_text="Un membre du compte ; il recevra les réponses. Laissez vide s'il n'en a pas.",
    )
    canal = forms.ChoiceField(label="Arrivée par")
    categorie = forms.ChoiceField(label="Catégorie")
    objet = forms.CharField(max_length=200)
    corps = forms.CharField(label="Demande", widget=forms.Textarea(attrs={"rows": 6}))

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["canal"].choices = [
            c for c in Ticket.Canal.choices if c[0] != Ticket.Canal.FORMULAIRE
        ]
        self.fields["categorie"].choices = Ticket.Categorie.choices
