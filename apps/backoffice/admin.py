"""Administration du back-office : écrans des agents EL-IMMEKA.

Servie dans l'espace du back-office seulement (voir config/urls.py).
"""

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import path

from apps.comptes import second_facteur
from apps.comptes.models import Client, Dossier, Piece, Utilisateur
from apps.core.models import Audit
from apps.fichiers.models import Fichier
from apps.fichiers.services import lien

from .forms import EnregistrementClientForm
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
