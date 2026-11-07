"""Tableau de bord du client (T-92). Le fragment est dans `fragments.py`."""

from django.shortcuts import render

from apps.comptes.permissions import permission_requise

from .fragments import contexte_tableau


@permission_requise("tableau.consulter")
def tableau_de_bord(request):
    return render(request, "campagnes/tableau_de_bord.html", contexte_tableau(request))
