"""Fragment du fil d'un ticket : mêmes droits que la page."""

from django.http import Http404
from django.shortcuts import render

from apps.comptes.permissions import permission_requise

from . import selectors


@permission_requise("support.consulter")
def fil(request, pk):
    trouve = selectors.ticket_du_compte(request.user.client_id, pk)
    if trouve is None:
        raise Http404
    return render(
        request, "support/_fil.html", {"ticket": trouve, "fil": selectors.fil_client(trouve)}
    )
