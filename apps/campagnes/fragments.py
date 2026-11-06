"""Fragments HTMX des campagnes : le suivi en direct (T-67).

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


@permission_requise("campagnes.composer")
def suivi(request, pk):
    campagne = get_object_or_404(Campagne, pk=pk)
    return render(request, "campagnes/_suivi.html", contexte_suivi(campagne) | {"hors_bande": True})
