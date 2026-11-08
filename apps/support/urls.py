from django.urls import path

from . import fragments, views

urlpatterns = [
    path("support", views.liste, name="support"),
    path("support/nouveau", views.nouveau, name="support-nouveau"),
    path("support/<uuid:pk>", views.ticket, name="support-ticket"),
    path("support/<uuid:pk>/fragments/fil", fragments.fil, name="support-ticket-fil"),
]
