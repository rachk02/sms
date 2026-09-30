"""Fragments HTMX de l'import. Mêmes contrôles de droits que les pages."""

from django.shortcuts import render

from apps.comptes.permissions import permission_requise

from .views import _contexte_suivi, _import


@permission_requise("contacts.importer")
def progression(request, pk):
    import_ = _import(request, pk)
    return render(request, "contacts/_progression.html", _contexte_suivi(import_))
