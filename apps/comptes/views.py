from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from apps.core.cloisonnement import poser_client
from apps.core.reseau import adresse_source

from . import jetons, second_facteur, services
from .authentification import DUREE_VERROUILLAGE, CompteVerrouille, ip_freinee, verrouille
from .models import CleApi, Client, Dossier, Utilisateur
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
        bloque = verrouille(courriel) or ip_freinee(adresse_source(request))
        contexte["erreur"] = MESSAGE_VERROUILLE if bloque else MESSAGE_ECHEC
    return render(request, "comptes/connexion.html", contexte)


@require_POST
def deconnexion(request):
    logout(request)
    messages.info(request, "Vous êtes déconnecté.")
    return redirect("connexion")


@permission_requise("compte.consulter")
def donnees(request):
    """Les exports du compte, sans passer par le support (T-89)."""
    return render(request, "comptes/donnees.html")


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


# Mot de passe oublié et changement de mot de passe (décision A2) -------------


def mot_de_passe_oubli(request):
    """Demande d'un lien de réinitialisation. La réponse est la même que
    l'adresse soit connue ou non."""
    if request.method == "POST":
        services.demander_reinitialisation(request.POST.get("courriel", ""))
        texte = (
            "Si un accès correspond à cette adresse, un lien pour choisir un nouveau mot "
            "de passe vient d'y être envoyé. Il est valable une heure."
        )
        return _page_lien(request, "Demande reçue", texte, renvoi=False)
    return render(request, "comptes/mot_de_passe_oubli.html")


def mot_de_passe_reinitialiser(request, jeton):
    try:
        utilisateur = jetons.lire(jeton, jetons.REINITIALISATION)
    except jetons.LienExpire:
        texte = "Ce lien a dépassé son heure de validité : demandez-en un nouveau."
        return _page_lien(request, EXPIRE, texte, renvoi=False, statut=410)
    except jetons.LienInvalide:
        texte = "Ce lien a déjà servi ou n'est pas valide."
        return _page_lien(request, INUTILISABLE, texte, renvoi=False, statut=404)
    erreurs = []
    if request.method == "POST":
        mot_de_passe = request.POST.get("mot_de_passe", "")
        if mot_de_passe != request.POST.get("confirmation", ""):
            erreurs = ["Les deux saisies ne concordent pas."]
        else:
            try:
                services.reinitialiser_mot_de_passe(jeton, mot_de_passe)
            except jetons.LienInvalide:
                texte = "Ce lien a déjà servi ou n'est pas valide."
                return _page_lien(request, INUTILISABLE, texte, renvoi=False, statut=404)
            except ValidationError as refus:
                erreurs = list(refus.messages)
            else:
                # Aucune session ouverte ici : la connexion, et le second facteur
                # s'il est réglé, restent à passer avec le nouveau mot de passe.
                logout(request)
                messages.success(request, "Votre mot de passe est modifié : connectez-vous.")
                return redirect("connexion")
    return render(
        request,
        "comptes/mot_de_passe_reinitialiser.html",
        {"utilisateur": utilisateur, "erreurs": erreurs},
    )


@login_required
def mot_de_passe_changer(request):
    erreurs = []
    if request.method == "POST":
        nouveau = request.POST.get("mot_de_passe", "")
        if nouveau != request.POST.get("confirmation", ""):
            erreurs = ["Les deux saisies ne concordent pas."]
        else:
            try:
                services.changer_mot_de_passe(request.user, request.POST.get("ancien", ""), nouveau)
            except CompteVerrouille:
                erreurs = [MESSAGE_VERROUILLE]
            except services.MotDePasseRefuse as refus:
                erreurs = [str(refus)]
            except ValidationError as refus:
                erreurs = list(refus.messages)
            else:
                # La session courante garde son empreinte ; les autres tombent.
                update_session_auth_hash(request, request.user)
                messages.success(request, "Votre mot de passe est modifié.")
                return redirect("accueil")
    return render(request, "comptes/mot_de_passe_changer.html", {"erreurs": erreurs})


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


# Clés d'API (T-106) ---------------------------------------------------------


@permission_requise("cles.gerer")
def cles_api(request):
    """Liste les clés du compte et en crée une. La clé complète n'est montrée
    que dans la réponse à la création, jamais rangée en session ni ailleurs."""
    erreur, nouvelle = "", None
    if request.method == "POST":
        try:
            _, nouvelle = services.creer_cle(
                client=request.user.client,
                nom=request.POST.get("nom", ""),
                environnement=request.POST.get("environnement", CleApi.Environnement.LIVE),
                auteur=request.user,
                adresse_ip=_ip(request),
            )
        except services.CleRefusee as refus:
            erreur = str(refus)
    contexte = {
        "cles": CleApi.objects.order_by("-cree_le"),
        "environnements": CleApi.Environnement.choices,
        "nouvelle": nouvelle,
        "erreur": erreur,
    }
    reponse = render(request, "comptes/cles_api.html", contexte)
    reponse["Cache-Control"] = "no-store"
    return reponse


@require_POST
@permission_requise("cles.gerer")
def revoquer_cle(request, pk):
    cle = get_object_or_404(CleApi, pk=pk)
    services.revoquer_cle(cle, auteur=request.user, adresse_ip=_ip(request))
    messages.success(request, f"La clé {cle.nom} est révoquée.")
    return redirect("cles-api")
