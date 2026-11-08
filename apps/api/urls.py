from django.urls import path

from . import rappels, views

urlpatterns = [
    path("rappels/jasmin/<uuid:connecteur_id>", rappels.rappel_jasmin, name="rappel-jasmin"),
    # API publique, version 1 (docs/api/politique-de-version.md).
    path("v1/messages", views.messages, name="api-messages"),
    path("v1/messages/<uuid:message_id>", views.message, name="api-message"),
    path("v1/solde", views.solde_du_compte, name="api-solde"),
    path("v1/expediteurs", views.expediteurs, name="api-expediteurs"),
    # Documentation publique, issue du code (T-110).
    path("v1/openapi.json", views.schema_openapi, name="api-openapi"),
    path("documentation/api", views.documentation_api, name="api-documentation"),
    # Écrans du compte pour les webhooks (T-111).
    path("compte/webhooks", views.webhooks, name="webhooks"),
    path("compte/webhooks/livraisons", views.livraisons, name="webhook-livraisons"),
    path(
        "compte/webhooks/<uuid:pk>/desactiver", views.desactiver_webhook, name="webhook-desactiver"
    ),
    path("compte/webhooks/<uuid:pk>/reactiver", views.reactiver_webhook, name="webhook-reactiver"),
    path("compte/alerte-solde", views.alerte_solde, name="alerte-solde"),
]
