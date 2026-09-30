from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from .authentification import DUREE_VERROUILLAGE, verrouille
from .models import Client, Dossier

MESSAGE_ECHEC = "Courriel ou mot de passe incorrect."
MESSAGE_VERROUILLE = (
    "Trop de tentatives : les connexions à ce compte sont suspendues pendant "
    f"{int(DUREE_VERROUILLAGE.total_seconds() // 60)} minutes."
)


def _suivant(request) -> str:
    suivant = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(suivant, allowed_hosts={request.get_host()}):
        return suivant
    return ""


def connexion(request):
    contexte = {"suivant": _suivant(request), "courriel": "", "erreur": ""}
    contexte["expiree"] = bool(request.GET.get("next")) and request.method == "GET"
    if request.method == "POST":
        courriel = request.POST.get("courriel", "")
        utilisateur = authenticate(
            request, courriel=courriel, password=request.POST.get("mot_de_passe", "")
        )
        if utilisateur is not None and utilisateur.client_id is not None:
            login(request, utilisateur)
            return redirect(contexte["suivant"] or "accueil")
        # Même message qu'une adresse existe ou non : seul le verrouillage
        # change le texte, et il frappe une adresse inconnue comme une autre.
        contexte["courriel"] = courriel
        contexte["erreur"] = MESSAGE_VERROUILLE if verrouille(courriel) else MESSAGE_ECHEC
    return render(request, "comptes/connexion.html", contexte)


@require_POST
def deconnexion(request):
    logout(request)
    messages.info(request, "Vous êtes déconnecté.")
    return redirect("connexion")


@login_required
def accueil(request):
    client = request.user.client
    dossier = Dossier.objects.filter(client=client).order_by("-cree_le").first()
    return render(
        request,
        "comptes/accueil.html",
        {
            "client": client,
            "dossier": dossier,
            "bac_a_sable": client.palier == Client.Palier.DECOUVERTE,
        },
    )
