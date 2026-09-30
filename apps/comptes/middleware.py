from urllib.parse import quote

from django.conf import settings
from django.shortcuts import redirect
from django.urls import reverse

from . import second_facteur

CLE_VERIFIE = "second_facteur_verifie"
CLE_CODES = "codes_secours_a_noter"

# Chemins ouverts sans second facteur : ceux qui servent à le régler ou à le
# présenter, la sortie, et les points d'entrée machine.
LIBRES = (
    "/second-facteur/",
    "/connexion",
    "/deconnexion",
    "/admin/login/",
    "/admin/logout/",
    "/static/",
    "/rappels/",
    "/metriques",
)


def accueil_de_l_espace() -> str:
    return "/admin/" if settings.ESPACE == "backoffice" else "/"


class SecondFacteurMiddleware:
    """Après le mot de passe, le second facteur : aucune page ne s'ouvre tant
    qu'il n'est pas présenté, ou, pour un rôle d'administration dont le délai de
    grâce est passé, tant qu'il n'est pas réglé. Vaut pour le portail comme
    pour l'administration du back-office."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        utilisateur = request.user
        if not utilisateur.is_authenticated or request.path.startswith(LIBRES):
            return self.get_response(request)
        if request.session.get(CLE_CODES):
            return redirect("second-facteur-codes")
        if request.session.get(CLE_VERIFIE):
            return self.get_response(request)
        if utilisateur.second_facteur_actif:
            cible = reverse("second-facteur-verifier")
            return redirect(f"{cible}?next={quote(request.get_full_path())}")
        if second_facteur.exige(utilisateur) and second_facteur.delai_de_grace_ecoule(utilisateur):
            return redirect("second-facteur-activer")
        return self.get_response(request)
