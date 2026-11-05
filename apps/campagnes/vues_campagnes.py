"""Écrans des campagnes : composition, aperçu, envoi de test, confirmation (T-61) ;
pause, reprise et annulation (T-64)."""

from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.comptes.permissions import autorise, permission_requise
from apps.contacts.models import Liste, Segment
from apps.core.encodage import table_pour_le_navigateur
from apps.core.montants import en_lettres, francs_cfa
from apps.expediteurs.models import Expediteur

from . import modeles, selectors, services
from .models import Campagne, Modele


def _campagne(pk) -> Campagne:
    # campagnes_campagne est cloisonnée : une campagne d'un autre compte est invisible.
    return get_object_or_404(Campagne.objects.select_related("client", "expediteur"), pk=pk)


@permission_requise("campagnes.composer")
def liste(request):
    campagnes = Campagne.objects.filter(client=request.user.client).order_by("-cree_le")[:100]
    return render(request, "campagnes/campagnes.html", {"campagnes": campagnes})


@permission_requise("campagnes.composer")
def nouvelle(request):
    client = request.user.client
    saisie = {"nom": "", "texte": "", "listes": [], "segment": "", "expediteur": "", "type": ""}
    erreur = ""
    if request.method == "POST":
        saisie = {
            "nom": request.POST.get("nom", ""),
            "texte": request.POST.get("texte", ""),
            "listes": request.POST.getlist("listes"),
            "segment": request.POST.get("segment", ""),
            "expediteur": request.POST.get("expediteur", ""),
            "politique": request.POST.get("politique", Campagne.Politique.REPLI),
            "type": request.POST.get("type", Campagne.Type.MARKETING),
        }
        try:
            campagne = services.creer(
                client=client,
                auteur=request.user,
                nom=saisie["nom"],
                texte=saisie["texte"],
                listes=saisie["listes"],
                segment_id=saisie["segment"] or None,
                expediteur_id=saisie["expediteur"] or None,
                politique=saisie["politique"],
                type_=saisie["type"],
            )
        except services.CampagneRefusee as refus:
            erreur = str(refus)
        else:
            return redirect("campagne", campagne.id)
    contexte = {
        "saisie": saisie,
        "erreur": erreur,
        "table_encodage": table_pour_le_navigateur(),
        "replis": modeles.replis_declares(client),
        "modeles": Modele.objects.filter(client=client).order_by("nom"),
        "listes": Liste.objects.filter(client=client).order_by("nom"),
        "segments": Segment.objects.filter(client=client, actif=True).order_by("nom"),
        "expediteurs": Expediteur.objects.filter(client=client)
        .exclude(etat=Expediteur.Etat.SUSPENDU)
        .order_by("libelle"),
        "politiques": Campagne.Politique.choices,
        "types": Campagne.Type.choices,
    }
    return render(request, "campagnes/nouvelle.html", contexte)


@permission_requise("campagnes.composer")
def detail(request, pk):
    campagne = _campagne(pk)
    contexte = {"campagne": campagne}
    if campagne.etat == Campagne.Etat.BROUILLON:
        vu = services.apercu(campagne)
        contexte |= {
            "apercu": vu,
            "par_expediteur": sorted(
                (
                    (action, expediteur, nombre)
                    for (action, expediteur), nombre in vu.par_expediteur.items()
                ),
                key=lambda ligne: -ligne[2],
            ),
            "destinataires_lettres": en_lettres(vu.destinataires),
            "cout_lettres": francs_cfa(vu.cout),
            "peut_lancer": autorise(request.user, "campagnes.lancer"),
        }
    else:
        contexte |= {
            "bilan": selectors.bilan(campagne),
            "peut_piloter": autorise(request.user, "campagnes.lancer"),
            "pausable": campagne.etat in services.PAUSABLES,
            "annulable": campagne.etat in services.ANNULABLES,
        }
    return render(request, "campagnes/campagne.html", contexte)


@require_POST
@permission_requise("campagnes.composer")
def envoyer_test(request, pk):
    campagne = _campagne(pk)
    try:
        message = services.envoyer_test(
            campagne, numero=request.POST.get("numero", ""), auteur=request.user
        )
    except services.CampagneRefusee as refus:
        messages.error(request, str(refus))
    else:
        messages.success(
            request,
            f"Test envoyé à {message.numero_normalise} sous {message.expediteur_libelle}, "
            f"facturé {message.cout_total} FCFA.",
        )
    return redirect("campagne", campagne.id)


@require_POST
@permission_requise("campagnes.lancer")
def confirmer(request, pk):
    campagne = _campagne(pk)
    try:
        lus = int(request.POST.get("destinataires", "")), int(request.POST.get("cout", ""))
    except ValueError:
        messages.error(request, "Chiffres de confirmation illisibles : rouvrez l'aperçu.")
        return redirect("campagne", campagne.id)
    try:
        services.confirmer(campagne, auteur=request.user, destinataires_lus=lus[0], cout_lu=lus[1])
    except services.CampagneRefusee as refus:
        messages.error(request, str(refus))
    else:
        messages.success(request, f"Campagne « {campagne.nom} » confirmée.")
    return redirect("campagne", campagne.id)


def _piloter(request, pk, action, succes: str):
    campagne = _campagne(pk)
    try:
        action(campagne, auteur=request.user)
    except services.CampagneRefusee as refus:
        messages.error(request, str(refus))
    else:
        messages.success(request, succes.format(nom=campagne.nom))
    return redirect("campagne", campagne.id)


@require_POST
@permission_requise("campagnes.lancer")
def mettre_en_pause(request, pk):
    return _piloter(
        request,
        pk,
        services.mettre_en_pause,
        "Campagne « {nom} » en pause : plus aucun message ne part. "
        "Ceux dont la soumission avait commencé finissent leur chemin.",
    )


@require_POST
@permission_requise("campagnes.lancer")
def reprendre(request, pk):
    return _piloter(request, pk, services.reprendre, "Campagne « {nom} » reprise.")


@require_POST
@permission_requise("campagnes.lancer")
def annuler(request, pk):
    return _piloter(
        request,
        pk,
        services.annuler,
        "Campagne « {nom} » annulée : les messages non partis sont annulés et leurs "
        "crédits libérés ; les messages déjà soumis au réseau restent partis.",
    )
