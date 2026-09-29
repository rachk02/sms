from django.contrib import admin

from .models import Audit


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
