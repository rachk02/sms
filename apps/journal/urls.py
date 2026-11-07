from django.urls import path

from . import views

urlpatterns = [
    path("journal", views.rechercher, name="journal"),
    path("journal/export", views.exporter, name="journal-export"),
]
