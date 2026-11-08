from django import forms

from .models import Ticket


class OuvertureForm(forms.Form):
    categorie = forms.ChoiceField(
        label="Catégorie",
        choices=[("", "Choisissez")] + list(Ticket.Categorie.choices),
        help_text="Elle fixe la priorité et le délai de première réponse.",
    )
    objet = forms.CharField(label="Objet", max_length=200)
    corps = forms.CharField(label="Votre demande", widget=forms.Textarea(attrs={"rows": 6}))
    piece = forms.FileField(label="Pièce jointe (PDF, JPEG ou PNG, 5 Mo au plus)", required=False)
    # L'objet d'où le ticket est ouvert : relu côté serveur, sur le compte.
    message = forms.UUIDField(required=False, widget=forms.HiddenInput)
    recu = forms.DateTimeField(required=False, widget=forms.HiddenInput)
    campagne = forms.UUIDField(required=False, widget=forms.HiddenInput)


class ReponseForm(forms.Form):
    corps = forms.CharField(label="Votre réponse", widget=forms.Textarea(attrs={"rows": 4}))
    piece = forms.FileField(label="Pièce jointe (PDF, JPEG ou PNG, 5 Mo au plus)", required=False)
