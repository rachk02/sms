from django.urls import path

from . import rappels

urlpatterns = [
    path("rappels/paiements/<str:fournisseur>", rappels.rappel_paiement, name="rappel-paiement"),
]
