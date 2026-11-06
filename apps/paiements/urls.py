from django.urls import path

from . import rappels, views

urlpatterns = [
    path("rappels/paiements/<str:fournisseur>", rappels.rappel_paiement, name="rappel-paiement"),
    path("paiements/recharger", views.recharger, name="recharger"),
    path("paiements/<uuid:pk>", views.paiement, name="paiement"),
    path("paiements/<uuid:pk>/fragments/statut", views.statut, name="paiement-statut"),
    path("paiements/simulateur/<str:reference>", views.simulateur, name="simulateur-paiement"),
    path("paiements/simulateur/<str:reference>/choix", views.simuler, name="simulateur-choix"),
]
