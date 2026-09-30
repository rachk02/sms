from django.urls import path

from . import views

urlpatterns = [
    path("expediteurs", views.liste, name="expediteurs"),
    path("expediteurs/declarer", views.declarer, name="expediteurs-declarer"),
]
