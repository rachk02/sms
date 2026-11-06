"""Parcours de rechargement (T-86) : écrans minimaux, en attendant la refonte de
l'interface. Toute décision passe par services.py et selectors.py."""

from django.contrib import messages
from django.http import Http404, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.comptes.permissions import permission_requise
from apps.core.evenements import publier
from apps.core.files import TOPOLOGIE
from apps.fichiers.services import ouvrir

from . import fournisseurs, rendu, selectors, services
from .models import Facture, Transaction
from .rappels import FILE


@permission_requise("credits.recharger")
def recharger(request):
    reglage = services.reglage()
    if request.method == "POST":
        try:
            credits_ht = int(request.POST.get("credits", ""))
        except ValueError:
            credits_ht = 0
        try:
            _, adresse = services.initier(
                request.user.client,
                credits_ht=credits_ht,
                fournisseur_nom=request.POST.get("fournisseur", ""),
                auteur=request.user,
            )
        except services.RechargementRefuse as refus:
            messages.error(request, str(refus))
        else:
            return redirect(adresse)
    contexte = {
        "fournisseurs": fournisseurs.actifs(),
        "reglage": reglage,
        "pas": services.pas_d_achat(reglage),
    }
    return render(request, "paiements/recharger.html", contexte)


def _contexte(pk) -> dict:
    # paiements_transaction est cloisonnée : celle d'un autre compte est invisible.
    transaction_ = get_object_or_404(Transaction, pk=pk)
    return {"transaction": transaction_, "suivi": selectors.suivi(transaction_)}


@permission_requise("credits.consulter")
def paiement(request, pk):
    return render(request, "paiements/paiement.html", _contexte(pk))


@permission_requise("credits.consulter")
def statut(request, pk):
    return render(request, "paiements/_statut.html", _contexte(pk))


def _simulation(reference: str) -> dict:
    if not fournisseurs.actifs().get("simule"):
        raise Http404
    simulation = fournisseurs.simulation(reference)
    if simulation is None:
        raise Http404
    return simulation


def simulateur(request, reference: str):
    """La page du fournisseur simulé, comme celle où l'opérateur fait valider le
    paiement. N'existe que si le simulateur est actif, jamais en production."""
    contexte = {"reference": reference, "simulation": _simulation(reference)}
    return render(request, "paiements/simulateur.html", contexte)


CHOIX = {"payer": "CONFIRME", "payer_sans_rappel": "CONFIRME", "refuser": "ECHOUE"}


@require_POST
def simuler(request, reference: str):
    """Le client paie ou refuse chez le fournisseur simulé, qui rappelle alors la
    plateforme, sauf si l'essai demande que le rappel se perde."""
    simulation = _simulation(reference)
    choix = request.POST.get("choix")
    if choix not in CHOIX:
        raise Http404
    fournisseurs.simuler(reference, montant=int(simulation["montant"]), etat=CHOIX[choix])
    if choix != "payer_sans_rappel":
        publier(
            TOPOLOGIE.nom(FILE),
            echange=TOPOLOGIE.echange,
            donnees={"fournisseur": "SIMULE", "reference": reference, "source": "simulateur"},
        )
    return redirect(simulation.get("retour") or "/")


# Factures (T-88)


@permission_requise("credits.consulter")
def factures(request):
    # paiements_facture est cloisonnée : seules celles du compte remontent.
    contexte = {"factures": selectors.factures(request.user.client)}
    return render(request, "paiements/factures.html", contexte)


@permission_requise("credits.consulter")
def facture(request, pk):
    """Le PDF archivé de la facture, ou, tant qu'il ne l'est pas encore, le même
    PDF rendu à partir des mentions figées à l'émission."""
    facture_ = get_object_or_404(Facture, pk=pk)
    if facture_.fichier_id:
        with ouvrir(facture_.fichier) as flux:
            contenu = flux.read()
    else:
        contenu = rendu.facture_pdf(facture_.mentions)
    reponse = HttpResponse(contenu, content_type="application/pdf")
    reponse["Content-Disposition"] = f'attachment; filename="{facture_.numero}.pdf"'
    return reponse
