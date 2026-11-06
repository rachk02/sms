"""Lectures du journal des messages. Aucune écriture."""

from datetime import datetime, timedelta

from django.db.models import Sum
from django.utils import timezone

from .models import Compteur, Message

# Fenêtre du rapprochement d'un STOP avec ses émetteurs (T-70).
FENETRE_DESINSCRIPTION = timedelta(hours=72)

# Borne basse sur la date de réception, clé de partition : elle écarte les
# partitions anciennes. Un message soumis dans la fenêtre a été reçu au plus
# trente jours plus tôt (pause ou programmation comprises) : au-delà, la plage
# des recherches du journal s'arrête aussi.
RECULE_RECEPTION = timedelta(days=30)


def clients_touches(numero_normalise: str, jusqu_a: datetime) -> dict:
    """Clients qui ont soumis au moins un message vers ce numéro dans les
    soixante-douze heures qui précèdent `jusqu_a`, avec l'expéditeur de leur
    dernier message soumis : celui que la personne a lu.

    La date qui compte est celle de la soumission : un message d'une campagne
    reprise après une longue pause est parti récemment, même reçu longtemps
    avant. Un message jamais soumis (refusé avant le réseau, annulé, encore en
    attente) n'a touché personne. Un message système n'est pas un envoi du
    client."""
    messages = (
        Message.objects.filter(
            numero_normalise=numero_normalise,
            nature=Message.Nature.CLIENT,
            date_reception__gte=jusqu_a - FENETRE_DESINSCRIPTION - RECULE_RECEPTION,
            date_reception__lte=jusqu_a,
            date_soumission__gte=jusqu_a - FENETRE_DESINSCRIPTION,
            date_soumission__lte=jusqu_a,
        )
        .order_by("client_id", "-date_soumission")
        .distinct("client_id")
        .values_list("client_id", "expediteur_libelle")
    )
    return dict(messages)


def debut_du_mois(maintenant: datetime | None = None) -> datetime:
    """Premier instant du mois civil, à l'heure de Ouagadougou."""
    local = timezone.localtime(maintenant or timezone.now())
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def volume_mensuel(client_id, maintenant: datetime | None = None) -> int:
    """SMS soumis par le client depuis le début du mois civil, lus sur les
    compteurs pré-agrégés des campagnes, jamais sur `journal_message`. Sert au
    palier de volume des grilles tarifaires (T-82). Les envois hors campagne n'y
    sont pas encore comptés, comme dans `journal_compteur`."""
    total = Compteur.objects.filter(
        client_id=client_id, heure__gte=debut_du_mois(maintenant)
    ).aggregate(total=Sum("emis"))["total"]
    return total or 0
