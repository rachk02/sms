from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.cloisonnement import poser_client

from . import jetons, second_facteur, services
from .authentification import DUREE_VERROUILLAGE, verrouille
from .models import Client, Dossier, Utilisateur
from .permissions import permission_requise

MESSAGE_ECHEC = "Courriel ou mot de passe incorrect."
MESSAGE_VERROUILLE = (
    "Trop de tentatives : les connexions à ce compte sont suspendues pendant "
    f"{int(DUREE_VERROUILLAGE.total_seconds() // 60)} minutes."
)


def _suivant(request) -> str:
    suivant = request.POST.get("next") or request.GET.get("next") or ""
    if url_has_allowed_host_and_scheme(suivant, allowed_hosts={request.get_host()}):
        return suivant
    return ""


def _ip(request) -> str | None:
    return request.META.get("REMOTE_ADDR")


def connexion(request):
    contexte = {"suivant": _suivant(request), "courriel": "", "erreur": ""}
    contexte["expiree"] = bool(request.GET.get("next")) and request.method == "GET"
    if request.method == "POST":
        courriel = request.POST.get("courriel", "")
        utilisateur = authenticate(
            request, courriel=courriel, password=request.POST.get("mot_de_passe", "")
        )
        if utilisateur is not None and utilisateur.client_id is not None:
            login(request, utilisateur)
            return redirect(contexte["suivant"] or "accueil")
        # Même message qu'une adresse existe ou non : seul le verrouillage
        # change le texte, et il frappe une adresse inconnue comme une autre.
        contexte["courriel"] = courriel
        contexte["erreur"] = MESSAGE_VERROUILLE if verrouille(courriel) else MESSAGE_ECHEC
    return render(request, "comptes/connexion.html", contexte)


@require_POST
def deconnexion(request):
    logout(request)
    messages.info(request, "Vous êtes déconnecté.")
    return redirect("connexion")


@permission_requise("compte.consulter")
def accueil(request):
    client = request.user.client
    dossier = Dossier.objects.filter(client=client).order_by("-cree_le").first()
    return render(
        request,
        "comptes/accueil.html",
        {
            "client": client,
            "dossier": dossier,
            "bac_a_sable": client.palier == Client.Palier.DECOUVERTE,
            "second_facteur_avant": (
                request.user.cree_le + second_facteur.DELAI_DE_GRACE
                if second_facteur.exige(request.user) and not request.user.second_facteur_actif
                else None
            ),
        },
    )


# Invitations et vérification d'adresse (T-32, T-36) ------------------------


def _page_lien(request, titre, message, renvoi=True, statut=200):
    contexte = {"titre": titre, "message": message, "renvoi": renvoi}
    return render(request, "comptes/lien.html", contexte, status=statut)


EXPIRE = "Lien expiré"
INUTILISABLE = "Lien inutilisable"


def invitation(request, jeton):
    try:
        membre = jetons.lire(jeton, jetons.INVITATION)
    except jetons.LienExpire:
        texte = "Ce lien d'invitation a dépassé ses sept jours de validité."
        return _page_lien(request, EXPIRE, texte, statut=410)
    except jetons.LienInvalide:
        texte = (
            "Ce lien a déjà servi ou n'est pas valide. Si votre accès est déjà créé, "
            "connectez-vous."
        )
        return _page_lien(request, INUTILISABLE, texte, statut=404)
    # Le lien identifie la personne : son compte est posé pour la requête, sans
    # quoi le cloisonnement le masquerait à cette page anonyme.
    poser_client(membre.client_id)
    erreurs = []
    if request.method == "POST":
        mot_de_passe = request.POST.get("mot_de_passe", "")
        if mot_de_passe != request.POST.get("confirmation", ""):
            erreurs = ["Les deux saisies ne concordent pas."]
        else:
            try:
                membre = services.accepter_invitation(jeton, mot_de_passe)
            except ValidationError as refus:
                erreurs = list(refus.messages)
            else:
                login(request, membre, backend=settings.AUTHENTICATION_BACKENDS[0])
                messages.success(request, "Votre accès est créé.")
                return redirect("accueil")
    return render(request, "comptes/invitation.html", {"membre": membre, "erreurs": erreurs})


def verification(request, jeton):
    try:
        services.verifier_courriel(jeton)
    except jetons.LienExpire:
        return _page_lien(request, EXPIRE, "Ce lien a dépassé ses vingt-quatre heures.", statut=410)
    except jetons.LienInvalide:
        texte = "Ce lien a déjà servi ou n'est pas valide."
        return _page_lien(request, INUTILISABLE, texte, statut=404)
    messages.success(request, "Votre adresse est vérifiée.")
    return redirect("connexion")


@require_POST
def renvoi(request):
    services.renvoyer_lien(request.POST.get("courriel", ""))
    texte = (
        "Si un accès en attente correspond à cette adresse, un nouveau lien vient d'y être envoyé."
    )
    return _page_lien(request, "Demande reçue", texte, renvoi=False)


# Membres du compte (T-36) ---------------------------------------------------


def _membre(request, pk) -> Utilisateur:
    # comptes_utilisateur n'est pas cloisonnée : le filtre sur le client est ici.
    return get_object_or_404(Utilisateur, pk=pk, client=request.user.client, is_active=True)


@permission_requise("membres.gerer")
def membres(request):
    erreur = ""
    if request.method == "POST":
        try:
            services.inviter(
                client=request.user.client,
                courriel=request.POST.get("courriel", ""),
                nom=request.POST.get("nom", ""),
                role=request.POST.get("role", ""),
                auteur=request.user,
                adresse_ip=_ip(request),
            )
        except services.MembreRefuse as refus:
            erreur = str(refus)
        else:
            messages.success(request, f"Invitation envoyée à {request.POST.get('courriel')}.")
            return redirect("membres")
    liste = Utilisateur.objects.filter(client=request.user.client, is_active=True).order_by("nom")
    contexte = {"membres": liste, "roles": services.ROLES_DE_LANCEMENT, "erreur": erreur}
    return render(request, "comptes/membres.html", contexte)


@require_POST
@permission_requise("membres.gerer")
def changer_role(request, pk):
    membre = _membre(request, pk)
    try:
        role = request.POST.get("role", "")
        services.changer_role(membre, role, auteur=request.user, adresse_ip=_ip(request))
        messages.success(request, f"Rôle de {membre.nom} modifié.")
    except services.MembreRefuse as refus:
        messages.error(request, str(refus))
    return redirect("membres")


@require_POST
@permission_requise("membres.gerer")
def retirer(request, pk):
    membre = _membre(request, pk)
    try:
        services.retirer_membre(membre, auteur=request.user, adresse_ip=_ip(request))
        messages.success(request, f"{membre.nom} n'a plus accès au compte.")
    except services.MembreRefuse as refus:
        messages.error(request, str(refus))
    return redirect("membres")
