from django.urls import path

from . import views, vues_campagnes

urlpatterns = [
    path("campagnes", vues_campagnes.liste, name="campagnes"),
    path("campagnes/nouvelle", vues_campagnes.nouvelle, name="campagne-nouvelle"),
    path("campagnes/modeles", views.liste_modeles, name="campagnes-modeles"),
    path("campagnes/modeles/<uuid:pk>", views.modifier_modele, name="campagnes-modele"),
    path("campagnes/<uuid:pk>", vues_campagnes.detail, name="campagne"),
    path("campagnes/<uuid:pk>/test", vues_campagnes.envoyer_test, name="campagne-test"),
    path("campagnes/<uuid:pk>/confirmer", vues_campagnes.confirmer, name="campagne-confirmer"),
    path("campagnes/<uuid:pk>/pause", vues_campagnes.mettre_en_pause, name="campagne-pause"),
    path("campagnes/<uuid:pk>/reprendre", vues_campagnes.reprendre, name="campagne-reprendre"),
    path("campagnes/<uuid:pk>/annuler", vues_campagnes.annuler, name="campagne-annuler"),
]
