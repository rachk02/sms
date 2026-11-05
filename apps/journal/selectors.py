"""Lectures du journal des messages. Aucune écriture."""

from datetime import datetime, timedelta

from .models import Message

# Fenêtre du rapprochement d'un STOP avec ses émetteurs (T-70).
FENETRE_DESINSCRIPTION = timedelta(hours=72)

# Borne basse sur la date de réception, clé de partition : elle écarte les
# partitions anciennes. Un message soumis dans la fenêtre a été reçu au plus
# trente jours plus tôt (pause ou programmation comprises) : au-delà, la plage
# des recherches du journal s'arrête aussi.
RECULE_RECEPTION = timedelta(days=30)


def clients_touches(numero_normalise: str, jusqu_a: datetime) -> list:
    """Clients qui ont soumis au moins un message vers ce numéro dans les
    soixante-douze heures qui précèdent `jusqu_a`.

    La date qui compte est celle de la soumission : un message d'une campagne
    reprise après une longue pause est parti récemment, même reçu longtemps
    avant. Un message jamais soumis (refusé avant le réseau, annulé, encore en
    attente) n'a touché personne."""
    return sorted(
        Message.objects.filter(
            numero_normalise=numero_normalise,
            date_reception__gte=jusqu_a - FENETRE_DESINSCRIPTION - RECULE_RECEPTION,
            date_reception__lte=jusqu_a,
            date_soumission__gte=jusqu_a - FENETRE_DESINSCRIPTION,
            date_soumission__lte=jusqu_a,
        )
        .order_by()
        .values_list("client_id", flat=True)
        .distinct(),
        key=str,
    )
