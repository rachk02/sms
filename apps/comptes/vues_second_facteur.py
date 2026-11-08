"""Pages du second facteur : activation, codes de secours, présentation."""

from django.contrib import messages
from django.contrib.auth import logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render
from django.utils.http import url_has_allowed_host_and_scheme

from . import second_facteur
from .middleware import CLE_CODES, CLE_VERIFIE, accueil_de_l_espace

CLE_SECRET = "second_facteur_secret_en_cours"


def _suivant(request) -> str:
    suivant = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(suivant, allowed_hosts={request.get_host()}):
        return suivant
    return accueil_de_l_espace()


@login_required
def activer(request):
    """Régler l'application : le secret n'est enregistré qu'une fois qu'un code
    a prouvé que l'application le connaît.

    Un second facteur déjà actif ne se règle pas de nouveau ici : la page est
    ouverte avant sa présentation, et le seul mot de passe suffirait sinon à
    remplacer le secret et à ouvrir la session. Un téléphone perdu passe par
    la réinitialisation, faite par un agent."""
    if request.user.second_facteur_actif:
        return redirect("second-facteur-verifier")
    secret = request.session.get(CLE_SECRET) or second_facteur.nouveau_secret()
    request.session[CLE_SECRET] = secret
    erreur = ""
    if request.method == "POST":
        try:
            codes = second_facteur.activer(request.user, secret, request.POST.get("code", ""))
        except second_facteur.CodeRefuse as refus:
            erreur = str(refus)
        else:
            del request.session[CLE_SECRET]
            request.session.cycle_key()
            request.session[CLE_VERIFIE] = True
            request.session[CLE_CODES] = codes
            return redirect("second-facteur-codes")
    uri = second_facteur.adresse(request.user, secret)
    contexte = {
        "qr": second_facteur.qr_svg(uri),
        "secret": " ".join(secret[i : i + 4] for i in range(0, len(secret), 4)),
        "erreur": erreur,
        "impose": second_facteur.exige(request.user),
    }
    return render(request, "comptes/second_facteur_activer.html", contexte)


@login_required
def codes(request):
    """Les codes de secours, montrés une seule fois : la page revient tant que
    l'utilisateur n'a pas confirmé les avoir notés."""
    liste = request.session.get(CLE_CODES)
    if not liste:
        return redirect(accueil_de_l_espace())
    erreur = ""
    if request.method == "POST":
        if request.POST.get("notes") == "oui":
            del request.session[CLE_CODES]
            messages.success(request, "Second facteur activé.")
            return redirect(accueil_de_l_espace())
        erreur = "Confirmez avoir noté vos codes : ils ne seront plus affichés."
    return render(request, "comptes/second_facteur_codes.html", {"codes": liste, "erreur": erreur})


@login_required
def verifier(request):
    erreur = ""
    if request.method == "POST":
        if second_facteur.verifier(request.user, request.POST.get("code", "")):
            request.session.cycle_key()
            request.session[CLE_VERIFIE] = True
            return redirect(_suivant(request))
        if second_facteur.bloque(request.user):
            logout(request)
            messages.error(request, "Trop de codes refusés : réessayez dans quinze minutes.")
            return redirect("connexion")
        erreur = "Code refusé."
    contexte = {"erreur": erreur, "suivant": _suivant(request)}
    return render(request, "comptes/second_facteur_verifier.html", contexte)
