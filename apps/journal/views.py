"""Export du journal d'envois par le client lui-même (T-89).

La période est obligatoire et bornée : jamais de lecture sans borne sur
journal_message. La réponse est lue après la fin de la transaction de la
requête, donc le générateur pose le client lui-même, dans sa propre
transaction.
"""

from django.db import transaction
from django.http import HttpResponseBadRequest, StreamingHttpResponse

from apps.comptes.permissions import permission_requise
from apps.core.cloisonnement import poser_client

from . import selectors


def _cloisonne(client_id, debut, fin):
    with transaction.atomic():
        poser_client(client_id)
        yield from selectors.export_csv(client_id, debut, fin)


@permission_requise("compte.consulter")
def exporter(request):
    try:
        debut, fin = selectors.periode_d_export(request.GET.get("debut"), request.GET.get("fin"))
    except ValueError as refus:
        return HttpResponseBadRequest(str(refus))
    reponse = StreamingHttpResponse(
        _cloisonne(request.user.client_id, debut, fin), content_type="text/csv; charset=utf-8"
    )
    nom = f"journal-{debut:%Y%m%d}-{fin:%Y%m%d}.csv"
    reponse["Content-Disposition"] = f'attachment; filename="{nom}"'
    return reponse
