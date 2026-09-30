"""Écran des identifiants d'expéditeur, côté client (T-58, T-59).

Le client déclare et consulte ; il ne décide jamais d'un état réseau. Aucune
route ici ne touche à `ExpediteurReseau`, et la base le refuserait de toute
façon (voir la migration de cloisonnement des expéditeurs).
"""

from django.contrib import messages
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.comptes.permissions import autorise, permission_requise

from . import services
from .models import OPERATEURS, Expediteur


@permission_requise("expediteurs.consulter")
def liste(request):
    expediteurs = []
    for expediteur in (
        Expediteur.objects.filter(client=request.user.client)
        .prefetch_related("reseaux")
        .order_by("libelle")
    ):
        par_operateur = {reseau.operateur: reseau for reseau in expediteur.reseaux.all()}
        expediteurs.append(
            {"expediteur": expediteur, "reseaux": [par_operateur.get(o) for o in OPERATEURS]}
        )
    contexte = {
        "expediteurs": expediteurs,
        "operateurs": OPERATEURS,
        "peut_declarer": autorise(request.user, "expediteurs.declarer"),
        "longueur_max": services.LONGUEUR_MAX,
    }
    return render(request, "expediteurs/liste.html", contexte)


@require_POST
@permission_requise("expediteurs.declarer")
def declarer(request):
    mandat = request.FILES.get("mandat")
    try:
        if mandat is None:
            raise services.ExpediteurRefuse("Joignez la pièce de mandat.")
        expediteur = services.declarer(
            client=request.user.client,
            libelle=request.POST.get("libelle", ""),
            mandat=mandat.file,
            nom_mandat=mandat.name,
            auteur=request.user,
        )
    except services.ExpediteurRefuse as refus:
        messages.error(request, str(refus))
    else:
        texte = (
            f"{expediteur.libelle} déclaré : nos équipes déposent le dossier chez chaque réseau."
        )
        if expediteur.revue_manuelle:
            texte += " Ce nom demande une vérification manuelle avant tout dépôt."
        messages.success(request, texte)
    return redirect("expediteurs")
