from django.urls import path

from . import views, vues_second_facteur

urlpatterns = [
    path("connexion", views.connexion, name="connexion"),
    path("deconnexion", views.deconnexion, name="deconnexion"),
    path("invitation/<str:jeton>", views.invitation, name="invitation"),
    path("verification/<str:jeton>", views.verification, name="verification"),
    path("renvoi", views.renvoi, name="renvoi"),
    path("mot-de-passe/oubli", views.mot_de_passe_oubli, name="mot-de-passe-oubli"),
    path(
        "mot-de-passe/reinitialiser/<str:jeton>",
        views.mot_de_passe_reinitialiser,
        name="mot-de-passe-reinitialiser",
    ),
    path("compte/mot-de-passe", views.mot_de_passe_changer, name="mot-de-passe-changer"),
    path("membres", views.membres, name="membres"),
    path("membres/<uuid:pk>/role", views.changer_role, name="membre-role"),
    path("membres/<uuid:pk>/retirer", views.retirer, name="membre-retirer"),
    path("second-facteur/activer", vues_second_facteur.activer, name="second-facteur-activer"),
    path("second-facteur/codes", vues_second_facteur.codes, name="second-facteur-codes"),
    path("second-facteur/verifier", vues_second_facteur.verifier, name="second-facteur-verifier"),
    path("compte/donnees", views.donnees, name="donnees"),
    path("compte/cles-api", views.cles_api, name="cles-api"),
    path("compte/cles-api/<uuid:pk>/revoquer", views.revoquer_cle, name="cle-api-revoquer"),
    path("", views.accueil, name="accueil"),
]
