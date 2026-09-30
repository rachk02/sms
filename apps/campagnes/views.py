"""Écrans des modèles de message (T-55, T-57)."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render

from apps.comptes.permissions import permission_requise
from apps.core.encodage import table_pour_le_navigateur

from . import modeles
from .models import Modele
from .personnalisation import ModeleInvalide


def _contexte_editeur(request, **contexte) -> dict:
    return {
        "table_encodage": table_pour_le_navigateur(),
        "replis": modeles.replis_declares(request.user.client),
        **contexte,
    }


@permission_requise("campagnes.composer")
def liste_modeles(request):
    client = request.user.client
    erreur, saisie = "", {"nom": "", "texte": ""}
    if request.method == "POST":
        saisie = {"nom": request.POST.get("nom", ""), "texte": request.POST.get("texte", "")}
        try:
            modele = modeles.enregistrer(client=client, **saisie)
        except ModeleInvalide as refus:
            erreur = str(refus)
        else:
            messages.success(request, f"Modèle « {modele.nom} » enregistré.")
            return redirect("campagnes-modeles")
    contexte = _contexte_editeur(
        request,
        modeles=Modele.objects.filter(client=client).order_by("nom"),
        saisie=saisie,
        erreur=erreur,
    )
    return render(request, "campagnes/modeles.html", contexte)


@permission_requise("campagnes.composer")
def modifier_modele(request, pk):
    # campagnes_modele est cloisonnée : un modèle d'un autre compte est invisible.
    modele = get_object_or_404(Modele, pk=pk)
    erreur, saisie = "", {"nom": modele.nom, "texte": modele.texte}
    if request.method == "POST":
        saisie = {"nom": request.POST.get("nom", ""), "texte": request.POST.get("texte", "")}
        try:
            modeles.enregistrer(client=request.user.client, modele=modele, **saisie)
        except ModeleInvalide as refus:
            erreur = str(refus)
        else:
            messages.success(request, f"Modèle « {modele.nom} » enregistré.")
            return redirect("campagnes-modeles")
    contexte = _contexte_editeur(request, modele=modele, saisie=saisie, erreur=erreur)
    return render(request, "campagnes/modele.html", contexte)
