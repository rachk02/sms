from django.urls import path

from . import rappels, views

urlpatterns = [
    path("rappels/jasmin/<uuid:connecteur_id>", rappels.rappel_jasmin, name="rappel-jasmin"),
    # API publique, version 1 (docs/api/politique-de-version.md).
    path("v1/messages", views.messages, name="api-messages"),
    path("v1/messages/<uuid:message_id>", views.message, name="api-message"),
    path("v1/solde", views.solde_du_compte, name="api-solde"),
    path("v1/expediteurs", views.expediteurs, name="api-expediteurs"),
]
