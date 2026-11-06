"""Administration du back-office : écrans des agents EL-IMMEKA.

Servie dans l'espace du back-office seulement (voir config/urls.py).
"""

from datetime import datetime, time

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.comptes import second_facteur
from apps.comptes.models import Client, Dossier, Piece, Utilisateur
from apps.conformite import services as conformite
from apps.conformite.models import Fenetre, JourFerie
from apps.core.models import Audit
from apps.core.numeros import NumeroInvalide, normaliser
from apps.expediteurs import services as expediteurs
from apps.expediteurs.models import Expediteur, ExpediteurReseau, Reserve
from apps.fichiers.models import Fichier
from apps.fichiers.services import lien
from apps.paiements import services as paiements
from apps.paiements.models import Reglage

from . import selectors
from .forms import EnregistrementClientForm
from .models import DossierPreuve
from .services import (
    CONTROLES_DE_REVUE,
    DecisionRefusee,
    EnregistrementRefuse,
    PieceDeposee,
    approuver_dossier,
    enregistrer_client,
    rejeter_dossier,
)


def adresse_ip(request) -> str | None:
    return request.META.get("REMOTE_ADDR")


@admin.register(Client)
class ClientAdmin(admin.ModelAdmin):
    list_display = ("raison_sociale", "forme", "rccm", "ifu", "palier", "etat", "cree_le")
    list_filter = ("forme", "palier", "etat")
    search_fields = ("raison_sociale", "rccm", "ifu")

    def add_view(self, request, form_url="", extra_context=None):
        """Enregistrer un client signé : identité, représentant, pièces."""
        formulaire = EnregistrementClientForm(request.POST or None, request.FILES or None)
        if request.method == "POST" and formulaire.is_valid():
            donnees = formulaire.cleaned_data
            pieces = {
                cle.removeprefix("piece_"): PieceDeposee(fichier.read(), fichier.name)
                for cle, fichier in donnees.items()
                if cle.startswith("piece_") and fichier
            }
            try:
                dossier = enregistrer_client(
                    identite=donnees,
                    pieces=pieces,
                    agent=request.user,
                    adresse_ip=adresse_ip(request),
                )
            except EnregistrementRefuse as refus:
                for champ, erreurs in refus.erreurs.items():
                    for erreur in erreurs:
                        formulaire.add_error(champ if champ in formulaire.fields else None, erreur)
            else:
                messages.success(
                    request, f"{dossier.client} enregistré ; dossier déposé pour revue."
                )
                return redirect("admin:comptes_dossier_change", dossier.id)
        contexte = {
            **self.admin_site.each_context(request),
            "title": "Enregistrer un client",
            "formulaire": formulaire,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/backoffice/enregistrer_client.html", contexte)


@admin.register(Dossier)
class DossierAdmin(admin.ModelAdmin):
    list_display = ("client", "etat", "depose_le", "decide_le", "decide_par")
    list_filter = ("etat",)
    # La file de revue se prend par ancienneté : le plus vieux dossier d'abord.
    ordering = ("depose_le",)

    def has_add_permission(self, request):
        return False  # un dossier naît de l'enregistrement d'un client

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        """Fiche de revue : pièces par liens signés, cinq contrôles, décision."""
        dossier = self.get_object(request, object_id)
        if dossier is None:
            return self._get_obj_does_not_exist_redirect(request, self.model._meta, object_id)
        if request.method == "POST":
            try:
                if "approuver" in request.POST:
                    approuver_dossier(
                        dossier.id,
                        agent=request.user,
                        controles=request.POST.getlist("controles"),
                        adresse_ip=adresse_ip(request),
                    )
                    messages.success(request, f"Dossier de {dossier.client} approuvé.")
                else:
                    rejeter_dossier(
                        dossier.id,
                        agent=request.user,
                        motif=request.POST.get("motif", ""),
                        detail=request.POST.get("detail", ""),
                        adresse_ip=adresse_ip(request),
                    )
                    messages.success(request, f"Dossier de {dossier.client} rejeté.")
                return redirect("admin:comptes_dossier_changelist")
            except DecisionRefusee as refus:
                messages.error(request, str(refus))
        pieces = []
        for piece in Piece.objects.filter(dossier=dossier).order_by("type"):
            fichier = Fichier.objects.filter(id=piece.fichier_id).first()
            pieces.append({"piece": piece, "lien": lien(fichier) if fichier else None})
        contexte = {
            **self.admin_site.each_context(request),
            "title": f"Revue du dossier de {dossier.client}",
            "dossier": dossier,
            "pieces": pieces,
            "controles": CONTROLES_DE_REVUE,
            "motifs": Dossier.MotifRejet.choices,
            "opts": self.model._meta,
            "decidable": dossier.etat in (Dossier.Etat.DEPOSE, Dossier.Etat.EN_REVUE),
        }
        return TemplateResponse(request, "admin/backoffice/revue_dossier.html", contexte)


@admin.register(Utilisateur)
class UtilisateurAdmin(admin.ModelAdmin):
    list_display = ("courriel", "nom", "client", "role", "is_active", "second_facteur_actif")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("courriel", "nom")
    exclude = (
        "password",
        "second_facteur_secret",
        "second_facteur_dernier_pas",
        "groups",
        "user_permissions",
    )

    def get_urls(self):
        propres = [
            path(
                "<uuid:pk>/reinitialiser-second-facteur/",
                self.admin_site.admin_view(self.reinitialiser_second_facteur),
                name="comptes_utilisateur_reinitialiser_second_facteur",
            )
        ]
        return propres + super().get_urls()

    def reinitialiser_second_facteur(self, request, pk):
        """Pour un utilisateur qui a perdu son second facteur : identité vérifiée
        par un canal indépendant, motif obligatoire, trace au journal d'audit."""
        utilisateur = Utilisateur.objects.get(pk=pk)
        if request.method == "POST":
            try:
                second_facteur.reinitialiser(
                    utilisateur,
                    agent=request.user,
                    motif=request.POST.get("motif", ""),
                    identite_verifiee=request.POST.get("identite_verifiee") == "oui",
                    adresse_ip=adresse_ip(request),
                )
            except second_facteur.CodeRefuse as refus:
                messages.error(request, str(refus))
            else:
                messages.success(request, f"Second facteur de {utilisateur} réinitialisé.")
                return redirect("admin:comptes_utilisateur_change", utilisateur.pk)
        contexte = {
            **self.admin_site.each_context(request),
            "title": f"Réinitialiser le second facteur de {utilisateur}",
            "utilisateur": utilisateur,
            "opts": self.model._meta,
        }
        return TemplateResponse(
            request, "admin/backoffice/reinitialiser_second_facteur.html", contexte
        )


@admin.register(Audit)
class AuditAdmin(admin.ModelAdmin):
    """Consultation seule : personne, super-administrateur compris, ne peut
    ajouter, modifier ou supprimer une entrée depuis l'administration."""

    list_display = ("horodatage", "action", "objet_type", "objet_id", "auteur", "client", "motif")
    list_filter = ("action", "objet_type", "horodatage")
    search_fields = ("objet_id", "motif", "auteur__courriel", "client__raison_sociale")
    date_hierarchy = "horodatage"
    readonly_fields = [champ.name for champ in Audit._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


def _date(valeur: str):
    """Une date saisie, à minuit à Ouagadougou ; None si vide ou invalide."""
    jour = parse_date(valeur or "")
    if jour is None:
        return None
    return timezone.make_aware(datetime.combine(jour, time.min))


@admin.register(Expediteur)
class ExpediteurAdmin(admin.ModelAdmin):
    """Fiche d'un identifiant : revue manuelle, dépôt et décision par réseau.
    Chaque décision passe par le service du module et laisse une trace au
    journal d'audit."""

    list_display = ("libelle", "client", "etat", "revue_manuelle", "cree_le")
    list_filter = ("etat", "revue_manuelle")
    search_fields = ("libelle", "client__raison_sociale")
    ordering = ("cree_le",)

    def has_add_permission(self, request):
        return False  # un identifiant se déclare depuis l'espace du client

    def has_delete_permission(self, request, obj=None):
        return False

    def change_view(self, request, object_id, form_url="", extra_context=None):
        expediteur = self.get_object(request, object_id)
        if expediteur is None:
            return self._get_obj_does_not_exist_redirect(request, self.model._meta, object_id)
        if request.method == "POST":
            operateur = request.POST.get("operateur", "")
            try:
                if "lever_revue" in request.POST:
                    expediteurs.lever_revue(
                        expediteur.id,
                        agent=request.user,
                        motif=request.POST.get("motif", ""),
                        adresse_ip=adresse_ip(request),
                    )
                    messages.success(request, "Revue levée : les dossiers peuvent être déposés.")
                elif "deposer" in request.POST:
                    expediteurs.deposer_dossier(
                        expediteur.id,
                        operateur,
                        agent=request.user,
                        reference=request.POST.get("reference", ""),
                        depose_le=_date(request.POST.get("date")),
                        adresse_ip=adresse_ip(request),
                    )
                    messages.success(request, f"Dépôt chez {operateur} enregistré.")
                elif "decider" in request.POST:
                    expediteurs.decider(
                        expediteur.id,
                        operateur,
                        agent=request.user,
                        approuve=request.POST.get("decision") == "approuve",
                        decide_le=_date(request.POST.get("date")),
                        reference=request.POST.get("reference", ""),
                        motif=request.POST.get("motif", ""),
                        expire_le=_date(request.POST.get("expire_le")),
                        adresse_ip=adresse_ip(request),
                    )
                    messages.success(request, f"Décision de {operateur} enregistrée.")
                return redirect("admin:expediteurs_expediteur_change", expediteur.id)
            except expediteurs.DecisionRefusee as refus:
                messages.error(request, str(refus))
        mandat = Fichier.objects.filter(id=expediteur.justificatif_id).first()
        contexte = {
            **self.admin_site.each_context(request),
            "title": f"Identifiant {expediteur.libelle}",
            "expediteur": expediteur,
            "reseaux": ExpediteurReseau.objects.filter(expediteur=expediteur).order_by("operateur"),
            "lien_mandat": lien(mandat) if mandat else None,
            "opts": self.model._meta,
        }
        return TemplateResponse(request, "admin/backoffice/expediteur.html", contexte)


@admin.register(Reserve)
class ReserveAdmin(admin.ModelAdmin):
    list_display = ("libelle", "motif", "actif")
    list_filter = ("actif",)
    search_fields = ("libelle",)


@admin.register(JourFerie)
class JourFerieAdmin(admin.ModelAdmin):
    """Jours fériés, saisis par un agent année par année : les fêtes mobiles ne
    se calculent pas. Chaque saisie est tracée au journal d'audit."""

    list_display = ("date", "libelle", "pays")
    list_filter = ("pays",)
    date_hierarchy = "date"
    search_fields = ("libelle",)

    def save_model(self, request, obj, form, change):
        conformite.enregistrer_jour_ferie(obj, agent=request.user, adresse_ip=adresse_ip(request))

    def delete_model(self, request, obj):
        conformite.supprimer_jour_ferie(obj, agent=request.user, adresse_ip=adresse_ip(request))

    def delete_queryset(self, request, queryset):
        for jour in queryset:
            self.delete_model(request, jour)


@admin.register(Fenetre)
class FenetreAdmin(admin.ModelAdmin):
    """Fenêtre légale des envois marketing, jour par jour, en heure locale. Un
    jour sans ligne est fermé."""

    list_display = ("jour", "ouverture", "fermeture", "pays")
    list_filter = ("pays",)
    ordering = ("pays", "jour")

    def save_model(self, request, obj, form, change):
        conformite.regler_fenetre(obj, agent=request.user, adresse_ip=adresse_ip(request))

    def delete_model(self, request, obj):
        conformite.fermer_jour(obj, agent=request.user, adresse_ip=adresse_ip(request))

    def delete_queryset(self, request, queryset):
        for fenetre in queryset:
            self.delete_model(request, fenetre)


@admin.register(Reglage)
class ReglageAdmin(admin.ModelAdmin):
    """Réglages du rechargement : TVA, bornes et délai d'attente. Une seule ligne,
    chaque changement tracé au journal d'audit."""

    list_display = ("__str__", "tva_active", "taux_tva", "credits_min", "credits_max")

    def has_add_permission(self, request):
        return not Reglage.objects.exists() and super().has_add_permission(request)

    def has_delete_permission(self, request, obj=None):
        return False

    def save_model(self, request, obj, form, change):
        paiements.regler(obj, agent=request.user, adresse_ip=adresse_ip(request))


@admin.register(DossierPreuve)
class DossierPreuveAdmin(admin.ModelAdmin):
    """Dossier de preuve d'un numéro (T-75) : ce que demandera l'ARCEP ou un
    abonné mécontent. Premier clic, l'entrée du menu ; deuxième, la recherche du
    numéro, qui montre chaque client qui le connaît ou lui a écrit ; troisième,
    les messages d'un client et la preuve de la désinscription."""

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def get_urls(self):
        propres = [
            path(
                "client/",
                self.admin_site.admin_view(self.client_view),
                name="backoffice_dossierpreuve_client",
            )
        ]
        return propres + super().get_urls()

    def _numero(self, request):
        saisi = request.GET.get("numero", "").strip()
        if not saisi:
            return saisi, None, ""
        try:
            return saisi, normaliser(saisi), ""
        except NumeroInvalide as refus:
            return saisi, None, f"Numéro invalide : {refus.cause}."

    def changelist_view(self, request, extra_context=None):
        saisi, numero, erreur = self._numero(request)
        contexte = {
            **self.admin_site.each_context(request),
            "title": "Dossier de preuve d'un numéro",
            "opts": self.model._meta,
            "saisi": saisi,
            "erreur": erreur,
            "dossier": selectors.dossier(numero) if numero else None,
        }
        return TemplateResponse(request, "admin/backoffice/dossier_preuve.html", contexte)

    def client_view(self, request):
        _, numero, erreur = self._numero(request)
        client = Client.objects.filter(id=request.GET.get("client") or None).first()
        if not numero or client is None:
            messages.error(request, erreur or "Numéro ou client introuvable.")
            return redirect("admin:backoffice_dossierpreuve_changelist")
        dossier = selectors.dossier(numero)
        ligne = next((ligne for ligne in dossier.clients if ligne.client == client), None)
        contexte = {
            **self.admin_site.each_context(request),
            "title": f"{numero} chez {client.raison_sociale}",
            "opts": self.model._meta,
            "numero": numero,
            "client": client,
            "ligne": ligne,
            "dossier": dossier,
            "messages_recus": selectors.messages_recus(numero, client.id),
            "plafond": selectors.MESSAGES_AFFICHES,
        }
        return TemplateResponse(request, "admin/backoffice/dossier_preuve_client.html", contexte)
