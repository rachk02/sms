from django.conf import settings
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("", include("apps.backoffice.urls")),
    path("", include("apps.api.urls")),
    path("", include("apps.paiements.urls")),
    path("", include("apps.campagnes.urls")),
    path("", include("apps.contacts.urls")),
    path("", include("apps.journal.urls")),
    path("", include("apps.expediteurs.urls")),
    path("", include("apps.comptes.urls")),
]

# L'administration n'existe que dans l'espace du back-office : dans l'espace des
# clients, le rôle applicatif ne verrait de toute façon aucun compte.
if settings.ESPACE == "backoffice":
    urlpatterns.insert(0, path("admin/", admin.site.urls))
