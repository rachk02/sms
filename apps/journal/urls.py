from django.urls import path

from . import views

urlpatterns = [
    path("journal/export", views.exporter, name="journal-export"),
]
