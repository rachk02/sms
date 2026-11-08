"""Routes publiques de l'API, version 1.

Une vue décode la requête, appelle un service ou un selector et rend du JSON.
Elle ne décide rien : l'envoi passe par `campagnes.services.accepter_unitaires`,
la seule porte des envois hors campagne.
"""

import json

from django.contrib import messages as django_messages
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST

from apps.campagnes.services import LOT_MAXIMUM, CampagneRefusee, accepter_unitaires
from apps.comptes.models import Client
from apps.comptes.permissions import permission_requise
from apps.comptes.services import regler_seuil_alerte
from apps.credits.services import SoldeOccupe, solde
from apps.expediteurs.models import OPERATEURS
from apps.expediteurs.selectors import du_compte
from apps.journal import selectors as journal

from . import services
from . import webhooks as webhooks_services
from .auth import erreur, reponse, route_api
from .models import Livraison, PointReception

ENTETE_IDEMPOTENCE = "Idempotency-Key"


def _corps(request):
    """Le corps JSON, ou None s'il ne se lit pas."""
    try:
        return json.loads(request.body or b"{}")
    except ValueError, UnicodeDecodeError:
        return None


def _sort(envoi) -> dict:
    if not envoi.accepte:
        return {"rang": envoi.rang, "erreur": {"code": envoi.code, "message": envoi.motif}}
    message = envoi.message
    return {
        "rang": envoi.rang,
        "id": str(message.id),
        "etat": message.etat,
        "numero": message.numero_normalise,
        "reseau": message.operateur,
        "expediteur": message.expediteur_libelle,
        "segments": message.segments,
        "cout": message.cout_total,
        "recu_le": message.date_reception.isoformat(),
    }


def _envoyer(request):
    """POST /v1/messages : un message, ou un lot sous `messages`. Réponse 202
    dès l'acceptation, un identifiant par message ; le suivi est asynchrone."""
    corps = _corps(request)
    if not isinstance(corps, dict):
        return erreur(400, "CORPS_INVALIDE", "Le corps doit être un objet JSON.")
    demandes = corps["messages"] if "messages" in corps else [corps]
    if not isinstance(demandes, list) or not all(isinstance(d, dict) for d in demandes):
        return erreur(400, "CORPS_INVALIDE", "`messages` doit être une liste d'objets.")
    if not demandes:
        return erreur(400, "CORPS_INVALIDE", "Aucun message à envoyer.")
    if len(demandes) > LOT_MAXIMUM:
        return erreur(400, "LOT_TROP_GRAND", f"Un lot compte au plus {LOT_MAXIMUM} messages.")
    cle = request.headers.get(ENTETE_IDEMPOTENCE, "").strip()
    if len(cle) > services.LONGUEUR_MAX:
        return erreur(
            400,
            "IDEMPOTENCE_INVALIDE",
            f"La clé d'idempotence dépasse {services.LONGUEUR_MAX} caractères.",
        )
    if cle:
        empreinte = services.empreinte(request.method, request.path, request.body)
        reservation = services.reserver(cle, empreinte)
        if reservation.reutilisee:
            return erreur(
                422,
                "IDEMPOTENCE_REUTILISEE",
                "Cette clé d'idempotence a déjà servi pour une autre requête.",
            )
        if not reservation.nouvelle:
            return reponse(
                reservation.reponse, reservation.statut_http, {"Idempotent-Rejoue": "true"}
            )
    try:
        sorts = accepter_unitaires(
            request.cle_api.client, demandes, cle_api=request.cle_api.prefixe
        )
    except SoldeOccupe:
        # Rien n'est gardé : un nouvel essai avec la même clé sera traité.
        return erreur(
            503,
            "SOLDE_OCCUPE",
            "Le solde du compte est occupé par une autre opération ; réessayez.",
            entetes={"Retry-After": "1"},
        )
    except CampagneRefusee as refus:
        statut, donnees = 422, {"erreur": {"code": "ENVOI_REFUSE", "message": str(refus)}}
    else:
        acceptes = sum(1 for envoi in sorts if envoi.accepte)
        donnees = {
            "acceptes": acceptes,
            "rejetes": len(sorts) - acceptes,
            "messages": [_sort(envoi) for envoi in sorts],
        }
        statut = 202 if acceptes else 422
    if cle:
        services.garder(cle, statut, donnees)
    return reponse(donnees, statut)


def _instant(valeur) -> str | None:
    return valeur.isoformat() if valeur else None


def _ligne_du_journal(message) -> dict:
    return {
        "id": str(message.id),
        "etat": message.etat,
        "numero": message.numero_normalise,
        "reseau": message.operateur,
        "expediteur": message.expediteur_libelle,
        "cout": message.cout_total,
        "motif_echec": message.motif_echec or None,
        "campagne_id": str(message.campagne_id) if message.campagne_id else None,
        "recu_le": message.date_reception.isoformat(),
    }


LIMITE_PAR_DEFAUT, LIMITE_MAXIMUM = 50, 200


def _journal(request):
    """GET /v1/messages : le journal, borné par une plage de dates comme à
    l'écran (`debut` et `fin`, AAAA-MM-JJ ; la dernière semaine sans aucune
    des deux, 31 jours au plus), des plus récents aux plus anciens, paginé par
    curseur sur (date de réception, id) : `apres` reprend où la page s'est
    arrêtée, même si des messages arrivent entretemps."""
    try:
        filtres = journal.lire_filtres(request.GET)
        limite = int(request.GET.get("limite") or LIMITE_PAR_DEFAUT)
        if not 1 <= limite <= LIMITE_MAXIMUM:
            raise journal.RechercheRefusee(f"`limite` va de 1 à {LIMITE_MAXIMUM}.")
        page = journal.rechercher(
            request.cle_api.client_id, filtres, request.GET.get("apres") or None, limite
        )
    except ValueError as refus:
        # RechercheRefusee en est une, comme une limite qui n'est pas un nombre.
        motif = (
            str(refus)
            if isinstance(refus, journal.RechercheRefusee)
            else "`limite` doit être un nombre entier."
        )
        return erreur(400, "RECHERCHE_REFUSEE", motif)
    return reponse(
        {
            "messages": [_ligne_du_journal(m) for m in page.messages],
            "suivant": page.suivant,
            "a_suivre": page.suivant is not None,
        }
    )


@route_api("GET", "POST")
def messages(request):
    return _envoyer(request) if request.method == "POST" else _journal(request)


@route_api("GET")
def message(request, message_id):
    """GET /v1/messages/<id> : le statut d'un message et son historique daté."""
    fiche = journal.fiche_par_identifiant(request.cle_api.client_id, message_id)
    if fiche is None:
        return erreur(
            404, "MESSAGE_INCONNU", "Aucun message de ce compte ne porte cet identifiant."
        )
    m = fiche.message
    return reponse(
        {
            **_ligne_du_journal(m),
            "segments": m.segments,
            "encodage": m.encodage,
            "soumis_le": _instant(m.date_soumission),
            "accuse_le": _instant(m.date_accuse),
            "diagnostic": fiche.diagnostic or None,
            "historique": [
                {"etat": etape.etat, "horodatage": etape.horodatage.isoformat()}
                for etape in fiche.etapes
            ],
        }
    )


@route_api("GET")
def solde_du_compte(request):
    """GET /v1/solde : acquis, réservé et disponible, en FCFA entiers."""
    lu = solde(request.cle_api.client_id)
    return reponse(
        {"acquis": lu.acquis, "reserve": lu.reserve, "disponible": lu.disponible, "devise": "XOF"}
    )


@route_api("GET")
def expediteurs(request):
    """GET /v1/expediteurs : les identifiants d'expéditeur et leur état sur
    chaque réseau (null : jamais soumis sur ce réseau)."""
    return reponse(
        {
            "expediteurs": [
                {
                    "libelle": ligne["expediteur"].libelle,
                    "etat": ligne["expediteur"].etat,
                    "par_defaut": ligne["expediteur"].par_defaut,
                    "reseaux": {
                        operateur: (
                            {"etat": reseau.etat, "expire_le": _instant(reseau.expire_le)}
                            if reseau
                            else None
                        )
                        for operateur, reseau in zip(OPERATEURS, ligne["reseaux"], strict=True)
                    },
                }
                for ligne in du_compte(request.cle_api.client_id)
            ]
        }
    )


# Écrans du compte : points de réception, seuil d'alerte, journal des livraisons
# (T-111). Réservés aux administrateurs du compte.

LIVRAISONS_AFFICHEES = 100


@permission_requise("webhooks.gerer")
def webhooks(request):
    """Liste les points de réception et en déclare un. Le secret de signature
    n'est montré que dans la réponse à la création."""
    erreur, secret = "", None
    if request.method == "POST":
        try:
            _, secret = webhooks_services.creer_point(
                client=request.user.client,
                url=request.POST.get("url", ""),
                evenements=request.POST.getlist("evenements"),
                auteur=request.user,
                adresse_ip=request.META.get("REMOTE_ADDR"),
            )
        except webhooks_services.PointRefuse as refus:
            erreur = str(refus)
    contexte = {
        "points": PointReception.objects.order_by("-cree_le"),
        "evenements": PointReception.Evenement.choices,
        "secret": secret,
        "erreur": erreur,
        "seuil": Client.objects.values_list("seuil_alerte_solde", flat=True).get(
            id=request.user.client_id
        ),
    }
    page = render(request, "api/webhooks.html", contexte)
    page["Cache-Control"] = "no-store"
    return page


@require_POST
@permission_requise("webhooks.gerer")
def desactiver_webhook(request, pk):
    point = get_object_or_404(PointReception, pk=pk)
    webhooks_services.desactiver_point(
        point, auteur=request.user, adresse_ip=request.META.get("REMOTE_ADDR")
    )
    django_messages.success(request, f"Le point {point.url} est désactivé.")
    return redirect("webhooks")


@require_POST
@permission_requise("webhooks.gerer")
def reactiver_webhook(request, pk):
    point = get_object_or_404(PointReception, pk=pk)
    try:
        webhooks_services.reactiver_point(
            point, auteur=request.user, adresse_ip=request.META.get("REMOTE_ADDR")
        )
    except webhooks_services.PointRefuse as refus:
        django_messages.error(request, str(refus))
    else:
        django_messages.success(request, f"Le point {point.url} est de nouveau actif.")
    return redirect("webhooks")


@require_POST
@permission_requise("webhooks.gerer")
def alerte_solde(request):
    try:
        regler_seuil_alerte(
            request.user.client,
            request.POST.get("seuil", ""),
            auteur=request.user,
            adresse_ip=request.META.get("REMOTE_ADDR"),
        )
    except ValueError as refus:
        django_messages.error(request, str(refus))
    else:
        django_messages.success(request, "Le seuil d'alerte est enregistré.")
    return redirect("webhooks")


@permission_requise("webhooks.gerer")
def livraisons(request):
    """Les dernières livraisons du compte, des plus récentes aux plus
    anciennes, avec leur état, leurs tentatives et la dernière réponse."""
    lignes = Livraison.objects.select_related("point").order_by("-cree_le", "-id")[
        :LIVRAISONS_AFFICHEES
    ]
    return render(
        request,
        "api/livraisons.html",
        {"livraisons": lignes, "limite": LIVRAISONS_AFFICHEES},
    )
