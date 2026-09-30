from django.urls import path

from . import views

urlpatterns = [
    path("connexion", views.connexion, name="connexion"),
    path("deconnexion", views.deconnexion, name="deconnexion"),
    path("invitation/<str:jeton>", views.invitation, name="invitation"),
    path("verification/<str:jeton>", views.verification, name="verification"),
    path("renvoi", views.renvoi, name="renvoi"),
    path("membres", views.membres, name="membres"),
    path("membres/<uuid:pk>/role", views.changer_role, name="membre-role"),
    path("membres/<uuid:pk>/retirer", views.retirer, name="membre-retirer"),
    path("", views.accueil, name="accueil"),
]
