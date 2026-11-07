from django.urls import path

from . import views

urlpatterns = [
    path("journal", views.rechercher, name="journal"),
    path("journal/export", views.exporter, name="journal-export"),
    path("journal/messages/<uuid:pk>", views.fiche_message, name="journal-message"),
]
