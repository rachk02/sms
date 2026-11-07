"""Fragments HTMX des campagnes : le suivi en direct (T-67) et le tableau de
bord (T-92).

Mêmes droits que la page : une campagne d'un autre compte est introuvable.
"""

from django.shortcuts import get_object_or_404, render

from apps.comptes.permissions import permission_requise

from . import selectors
from .models import Campagne


def contexte_suivi(campagne: Campagne) -> dict:
    return {
        "campagne": campagne,
        "suivi": selectors.suivi(campagne),
        "en_direct": selectors.en_direct(campagne),
        "liberation": selectors.liberation(campagne),
    }


def contexte_tableau(request) -> dict:
    tableau = selectors.tableau_de_bord(request.user.client_id, request.GET.get("periode"))
    return {"tableau": tableau, "periodes": selectors.PERIODES}


@permission_requise("campagnes.composer")
def suivi(request, pk):
    campagne = get_object_or_404(Campagne, pk=pk)
    return render(request, "campagnes/_suivi.html", contexte_suivi(campagne) | {"hors_bande": True})


@permission_requise("tableau.consulter")
def indicateurs(request):
    """Toutes les zones du tableau de bord en une réponse, par échange hors bande (T-92)."""
    return render(
        request, "campagnes/_indicateurs.html", contexte_tableau(request) | {"hors_bande": True}
    )
