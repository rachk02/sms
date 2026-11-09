from django.urls import path

from . import fragments, views, vues_gestion, vues_repertoire

urlpatterns = [
    path("contacts", vues_repertoire.repertoire, name="contacts-repertoire"),
    path("contacts/fragments/resultats", fragments.resultats, name="contacts-resultats"),
    path("contacts/export", vues_repertoire.exporter, name="contacts-exporter"),
    path(
        "contacts/exports/<uuid:pk>",
        vues_repertoire.telecharger_export,
        name="contacts-export-telecharger",
    ),
    path("contacts/listes", vues_gestion.voir_listes, name="contacts-listes"),
    path(
        "contacts/listes/<uuid:pk>/supprimer",
        vues_gestion.supprimer_liste,
        name="contacts-liste-supprimer",
    ),
    path("contacts/liste-noire", vues_gestion.voir_liste_noire, name="contacts-liste-noire"),
    path("contacts/champs", vues_gestion.voir_champs, name="contacts-champs"),
    path(
        "contacts/champs/<uuid:pk>/supprimer",
        vues_gestion.supprimer_champ,
        name="contacts-champ-supprimer",
    ),
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
