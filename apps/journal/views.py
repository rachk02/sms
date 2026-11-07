"""Recherche (T-93) et export (T-89) du journal d'envois par le client lui-même.

La période est obligatoire et bornée : jamais de lecture sans borne sur
journal_message. La réponse est lue après la fin de la transaction de la
requête, donc le générateur pose le client lui-même, dans sa propre
transaction.
"""

from django.db import transaction
from django.http import HttpResponseBadRequest, StreamingHttpResponse
from django.shortcuts import render
from django.utils.http import urlencode

from apps.comptes.permissions import permission_requise
from apps.core.cloisonnement import poser_client
from apps.core.etats import Etat

from . import selectors


def _cloisonne(client_id, debut, fin, filtres):
    with transaction.atomic():
        poser_client(client_id)
        yield from selectors.export_csv(client_id, debut, fin, filtres)


@permission_requise("compte.consulter")
def exporter(request):
    try:
        debut, fin = selectors.periode_d_export(request.GET.get("debut"), request.GET.get("fin"))
        filtres = selectors.lire_filtres(request.GET)
    except ValueError as refus:
        return HttpResponseBadRequest(str(refus))
    reponse = StreamingHttpResponse(
        _cloisonne(request.user.client_id, debut, fin, filtres),
        content_type="text/csv; charset=utf-8",
    )
    nom = f"journal-{debut:%Y%m%d}-{fin:%Y%m%d}.csv"
    reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
    return reponse


@permission_requise("compte.consulter")
def rechercher(request):
    contexte = {"etats": Etat.choices, "saisie": request.GET}
    try:
        filtres = selectors.lire_filtres(request.GET)
    except selectors.RechercheRefusee as refus:
        return render(request, "journal/recherche.html", contexte | {"erreur": str(refus)})
    contexte |= {"filtres": filtres, "export": urlencode(filtres.parametres())}
    if filtres.au_dela_du_seuil:
        return render(request, "journal/recherche.html", contexte | {"seuil": True})
    try:
        page = selectors.rechercher(request.user.client_id, filtres, request.GET.get("apres"))
    except selectors.RechercheRefusee as refus:
        return render(request, "journal/recherche.html", contexte | {"erreur": str(refus)})
    suivante = urlencode(filtres.parametres() | {"apres": page.suivant}) if page.suivant else None
    return render(
        request,
        "journal/recherche.html",
        contexte
        | {
            "page": page,
            "suivante": suivante,
            "premiere": not request.GET.get("apres"),
            "debut_recherche": urlencode(filtres.parametres()),
        },
    )
