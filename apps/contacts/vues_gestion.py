"""Écrans des listes, de la liste noire et des champs personnalisés.

Chaque écriture passe par le service du module ; la vue ne fait que relayer la
saisie et le refus éventuel, en clair.
"""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.comptes.permissions import permission_requise
from apps.core.numeros import NumeroInvalide, normaliser

from . import champs, liste_noire, listes
from .models import ChampPersonnalise, Liste, ListeNoire

AFFICHES = 50


@permission_requise("contacts.gerer")
def voir_listes(request):
    client = request.user.client
    if request.method == "POST":
        try:
            liste = listes.creer(
                client=client,
                nom=request.POST.get("nom", ""),
                description=request.POST.get("description", ""),
            )
        except listes.ListeRefusee as refus:
            messages.error(request, str(refus))
        else:
            messages.success(request, f"Liste « {liste.nom} » créée.")
        return redirect("contacts-listes")
    return render(
        request,
        "contacts/listes.html",
        {"listes": Liste.objects.filter(client=client).order_by("nom")},
    )


@require_POST
@permission_requise("contacts.gerer")
def supprimer_liste(request, pk):
    # contacts_liste est cloisonnée : une liste d'un autre compte est invisible.
    liste = get_object_or_404(Liste, pk=pk)
    membres = listes.supprimer(liste)
    messages.success(
        request,
        f"Liste « {liste.nom} » supprimée. Ses {membres} contacts restent dans le répertoire.",
    )
    return redirect("contacts-listes")


@permission_requise("contacts.gerer")
def voir_liste_noire(request):
    client = request.user.client
    if request.method == "POST":
        try:
            entree = liste_noire.bloquer(
                client=client,
                numero=request.POST.get("numero", ""),
                origine=ListeNoire.Origine.MANUEL,
                motif=request.POST.get("motif", "").strip(),
            )
        except NumeroInvalide as refus:
            messages.error(request, f"Numéro invalide : {refus.cause}.")
        else:
            messages.success(request, f"{entree.numero_normalise} ne recevra plus vos messages.")
        return redirect("contacts-liste-noire")
    # Les entrées du client seul : celles de la plateforme valent pour lui
    # mais tiennent à d'autres réclamations que les siennes.
    entrees = ListeNoire.objects.filter(client=client)
    cherche, erreur = request.GET.get("numero", "").strip(), ""
    if cherche:
        try:
            entrees = entrees.filter(numero_normalise=normaliser(cherche, client.pays))
        except NumeroInvalide as refus:
            erreur, entrees = f"Numéro invalide : {refus.cause}.", entrees.none()
    contexte = {
        "entrees": entrees.order_by("-ajoute_le", "-id")[:AFFICHES],
        "affiches": AFFICHES,
        "cherche": cherche,
        "erreur": erreur,
    }
    return render(request, "contacts/liste_noire.html", contexte)


@permission_requise("contacts.parametrer")
def voir_champs(request):
    client = request.user.client
    if request.method == "POST":
        try:
            champ = champs.declarer(
                client=client,
                libelle=request.POST.get("libelle", ""),
                valeur_repli=request.POST.get("valeur_repli", ""),
                type_=request.POST.get("type", ChampPersonnalise.Type.TEXTE),
            )
        except champs.ChampRefuse as refus:
            messages.error(request, str(refus))
        else:
            messages.success(request, f"Champ « {champ.libelle} » déclaré.")
        return redirect("contacts-champs")
    declares = ChampPersonnalise.objects.filter(client=client).order_by("libelle")
    contexte = {
        "champs": declares,
        "types": ChampPersonnalise.Type.choices,
        "maximum": ChampPersonnalise.MAXIMUM_PAR_CLIENT,
        "complet": len(declares) >= ChampPersonnalise.MAXIMUM_PAR_CLIENT,
    }
    return render(request, "contacts/champs.html", contexte)


@require_POST
@permission_requise("contacts.parametrer")
def supprimer_champ(request, pk):
    champ = get_object_or_404(ChampPersonnalise, pk=pk)
    desactives = champs.supprimer(champ)
    texte = f"Champ « {champ.libelle} » supprimé ; les valeurs restent sur les contacts."
    if desactives:
        noms = ", ".join(segment.nom for segment in desactives)
        texte += f" Segments désactivés, à revoir : {noms}."
    messages.success(request, texte)
    return redirect("contacts-champs")
