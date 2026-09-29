"""Administration du back-office : écrans des agents EL-IMMEKA.

Servie dans l'espace du back-office seulement (voir config/urls.py).
"""

from django.contrib import admin, messages
from django.shortcuts import redirect
from django.template.response import TemplateResponse

from apps.comptes.models import Client, Dossier, Piece, Utilisateur

from .forms import EnregistrementClientForm
from .services import EnregistrementRefuse, PieceDeposee, enregistrer_client


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


class PieceEnLigne(admin.TabularInline):
    model = Piece
    extra = 0
    fields = ("type", "etat", "expire_le", "motif_refus")
    readonly_fields = ("type",)
    can_delete = False


@admin.register(Dossier)
class DossierAdmin(admin.ModelAdmin):
    list_display = ("client", "etat", "depose_le", "decide_le", "decide_par")
    list_filter = ("etat",)
    # La file de revue se prend par ancienneté : le plus vieux dossier d'abord.
    ordering = ("depose_le",)
    inlines = [PieceEnLigne]
    readonly_fields = ("client", "depose_le", "decide_le", "decide_par")


@admin.register(Utilisateur)
class UtilisateurAdmin(admin.ModelAdmin):
    list_display = ("courriel", "nom", "client", "role", "is_active", "second_facteur_actif")
    list_filter = ("role", "is_active", "is_staff")
    search_fields = ("courriel", "nom")
    exclude = ("password", "second_facteur_secret", "groups", "user_permissions")
