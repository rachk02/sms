"""Billetterie, côté client (T-102). Le fragment du fil est dans `fragments.py`."""

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils.dateparse import parse_datetime

from apps.comptes.permissions import permission_requise
from apps.fichiers.services import FichierRefuse

from . import selectors, services
from .forms import OuvertureForm, ReponseForm


@permission_requise("support.consulter")
def liste(request):
    return render(
        request,
        "support/liste.html",
        {"tickets": selectors.tickets_du_compte(request.user.client_id)},
    )


def _contexte(client_id, donnees) -> tuple[dict, str]:
    """Le contexte technique de l'objet désigné, relu sur le compte. Un objet
    introuvable, ou d'un autre compte, rend une page introuvable."""
    if donnees.get("message"):
        recu = donnees.get("recu")
        recu = parse_datetime(recu) if isinstance(recu, str) else recu
        contexte = services.contexte_message(client_id, donnees["message"], recu) if recu else None
        if contexte is None:
            raise Http404
        return contexte, f"Message vers {contexte['numero']}"
    if donnees.get("campagne"):
        contexte = services.contexte_campagne(client_id, donnees["campagne"])
        if contexte is None:
            raise Http404
        return contexte, f"Campagne « {contexte['nom']} »"
    return {}, ""


@permission_requise("support.ouvrir")
def nouveau(request):
    if request.method == "POST":
        formulaire = OuvertureForm(request.POST, request.FILES)
        valide = formulaire.is_valid()
        donnees = formulaire.cleaned_data if valide else {}
    else:
        formulaire = OuvertureForm(initial=request.GET.dict())
        valide, donnees = False, request.GET.dict()
    contexte, rattache = _contexte(request.user.client_id, donnees)
    if valide:
        try:
            ticket = services.ouvrir(
                client=request.user.client,
                demandeur=request.user,
                categorie=donnees["categorie"],
                objet=donnees["objet"],
                corps=donnees["corps"],
                contexte=contexte,
                pieces=[donnees["piece"]] if donnees.get("piece") else [],
            )
        except (services.TicketRefuse, FichierRefuse) as refus:
            formulaire.add_error(None, str(refus))
        else:
            messages.success(request, f"Demande enregistrée sous la référence {ticket.reference}.")
            return redirect("support-ticket", ticket.id)
    return render(
        request,
        "support/nouveau.html",
        {"formulaire": formulaire, "contexte": contexte, "rattache": rattache},
    )


@permission_requise("support.consulter")
def ticket(request, pk):
    trouve = selectors.ticket_du_compte(request.user.client_id, pk)
    if trouve is None:
        raise Http404
    formulaire = ReponseForm()
    if request.method == "POST":
        formulaire = ReponseForm(request.POST, request.FILES)
        if formulaire.is_valid():
            pieces = [formulaire.cleaned_data["piece"]] if formulaire.cleaned_data["piece"] else []
            try:
                recu = services.repondre_client(
                    trouve,
                    auteur=request.user,
                    corps=formulaire.cleaned_data["corps"],
                    pieces=pieces,
                )
            except (services.TicketRefuse, FichierRefuse) as refus:
                formulaire.add_error(None, str(refus))
            else:
                if recu.id != trouve.id:
                    messages.success(
                        request,
                        f"Le ticket {trouve.reference} était clos depuis plus de sept jours : "
                        f"votre réponse ouvre le ticket {recu.reference}.",
                    )
                else:
                    messages.success(request, "Réponse envoyée.")
                return redirect("support-ticket", recu.id)
    return render(
        request,
        "support/ticket.html",
        {"ticket": trouve, "fil": selectors.fil_client(trouve), "formulaire": formulaire},
    )
