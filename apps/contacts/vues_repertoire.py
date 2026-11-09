"""Répertoire : recherche, pagination par curseur, demande d'export (T-53)."""

from urllib.parse import urlencode

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from apps.comptes.permissions import permission_requise

from . import recherche
from .models import Liste
from .recherche import Filtres, RechercheInvalide

OPERATEURS = ("ORANGE", "MOOV", "TELECEL")


def _filtres(donnees) -> Filtres:
    return Filtres(
        numero=donnees.get("numero", "").strip(),
        nom=donnees.get("nom", "").strip(),
        liste_id=donnees.get("liste", ""),
        operateur=donnees.get("operateur", "") if donnees.get("operateur") in OPERATEURS else "",
        depuis=donnees.get("depuis", ""),
        jusqu_au=donnees.get("jusqu_au", ""),
    )


def _requete(filtres: Filtres) -> str:
    """Les filtres en chaîne de requête, pour les liens de la page suivante."""
    return urlencode(
        {
            cle: valeur
            for cle, valeur in (
                ("numero", filtres.numero),
                ("nom", filtres.nom),
                ("liste", filtres.liste_id),
                ("operateur", filtres.operateur),
                ("depuis", filtres.depuis),
                ("jusqu_au", filtres.jusqu_au),
            )
            if valeur
        }
    )


def contexte_resultats(request) -> dict:
    client = request.user.client
    filtres = _filtres(request.GET)
    contexte = {"filtres": filtres, "requete": _requete(filtres), "contacts": [], "suivante": ""}
    try:
        contexte["contacts"], contexte["suivante"] = recherche.page(
            client, filtres, apres=request.GET.get("apres", "")
        )
        if not request.GET.get("apres"):
            contexte["au_dela"] = recherche.depasse_l_affichage(client, filtres)
    except RechercheInvalide as erreur:
        contexte["erreur"] = str(erreur)
    contexte["seuil"] = recherche.SEUIL_AFFICHAGE
    return contexte


@permission_requise("contacts.consulter")
def repertoire(request):
    contexte = contexte_resultats(request)
    contexte |= {
        "listes": Liste.objects.filter(client=request.user.client).order_by("nom"),
        "operateurs": OPERATEURS,
    }
    return render(request, "contacts/repertoire.html", contexte)


@require_POST
@permission_requise("contacts.exporter")
def exporter(request):
    filtres = _filtres(request.POST)
    try:
        recherche.demander_export(
            client=request.user.client, demandeur=request.user, filtres=filtres
        )
    except RechercheInvalide as erreur:
        messages.error(request, str(erreur))
    else:
        messages.success(
            request,
            "Export demandé : le lien de téléchargement vous parviendra à "
            f"{request.user.courriel}.",
        )
    requete = _requete(filtres)
    return redirect(f"/contacts?{requete}" if requete else "/contacts")


@permission_requise("contacts.exporter")
def telecharger_export(request, pk):
    """Le lien du courriel d'export : connexion et droits contrôlés ici, puis
    renvoi vers un lien signé de cinq minutes (décision A4)."""
    try:
        lien = recherche.lien_export(export_id=pk, utilisateur=request.user)
    except recherche.ExportIntrouvable:
        raise Http404 from None
    except recherche.ExportExpire:
        return render(request, "contacts/export_expire.html", status=410)
    return redirect(lien)
