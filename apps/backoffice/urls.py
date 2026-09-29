from django.urls import path

from . import views

urlpatterns = [
    path("metriques", views.metriques, name="metriques"),
]
