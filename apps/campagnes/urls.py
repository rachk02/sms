from django.urls import path

from . import views

urlpatterns = [
    path("campagnes/modeles", views.liste_modeles, name="campagnes-modeles"),
    path("campagnes/modeles/<uuid:pk>", views.modifier_modele, name="campagnes-modele"),
]
