from django.urls import path

from . import rappels

urlpatterns = [
    path("rappels/jasmin/<uuid:connecteur_id>", rappels.rappel_jasmin, name="rappel-jasmin"),
]
