"""Fragments HTMX des contacts. Mêmes contrôles de droits que les pages."""

from django.shortcuts import render

from apps.comptes.permissions import permission_requise

from .views import _contexte_suivi, _import
from .vues_repertoire import contexte_resultats


@permission_requise("contacts.importer")
def progression(request, pk):
    import_ = _import(request, pk)
    return render(request, "contacts/_progression.html", _contexte_suivi(import_))


@permission_requise("contacts.consulter")
def resultats(request):
    """Les résultats d'une recherche ; avec un curseur, les lignes suivantes seules."""
    gabarit = "contacts/_lignes.html" if request.GET.get("apres") else "contacts/_resultats.html"
    return render(request, gabarit, contexte_resultats(request))
