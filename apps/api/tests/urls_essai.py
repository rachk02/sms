"""Une route d'essai derrière `route_api`, pour éprouver l'authentification
avant que les routes publiques existent."""

from django.urls import path

from apps.api.auth import reponse, route_api
from apps.comptes.models import CleApi
from apps.core.cloisonnement import client_pose


@route_api("GET")
def qui(request):
    # Ce que la clé voit : son client posé, et les clés de ce client seul.
    return reponse(
        {
            "client": client_pose(),
            "prefixe": request.cle_api.prefixe,
            "cles_visibles": sorted(CleApi.objects.values_list("prefixe", flat=True)),
        }
    )


urlpatterns = [path("v1/essai", qui)]
