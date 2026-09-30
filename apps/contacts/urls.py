from django.urls import path

from . import fragments, views, vues_repertoire

urlpatterns = [
    path("contacts", vues_repertoire.repertoire, name="contacts-repertoire"),
    path("contacts/fragments/resultats", fragments.resultats, name="contacts-resultats"),
    path("contacts/export", vues_repertoire.exporter, name="contacts-exporter"),
    path("contacts/import", views.deposer, name="import-deposer"),
    path("contacts/import/<uuid:pk>/mappage", views.mappage, name="import-mappage"),
    path("contacts/import/<uuid:pk>", views.suivi, name="import-suivi"),
    path(
        "contacts/import/<uuid:pk>/fragments/progression",
        fragments.progression,
        name="import-progression",
    ),
    path("contacts/import/<uuid:pk>/rejets.csv", views.rejets, name="import-rejets"),
]
