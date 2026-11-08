"""Lectures de la billetterie. Côté client, une note interne n'est jamais lue."""

from django.utils import timezone

from . import engagement
from .models import MessageTicket, Ticket

LIMITE_LISTE = 100


def tickets_du_compte(client_id, limite: int = LIMITE_LISTE) -> list:
    """Les tickets du compte, les plus récents d'abord, bornés."""
    return list(
        Ticket.objects.filter(client_id=client_id)
        .select_related("demandeur")
        .order_by("-cree_le")[:limite]
    )


def ticket_du_compte(client_id, ticket_id) -> Ticket | None:
    return (
        Ticket.objects.filter(client_id=client_id, id=ticket_id)
        .select_related("demandeur", "precedent")
        .first()
    )


def fil_client(ticket: Ticket) -> list:
    """Le fil tel que le client le voit : sans les notes internes."""
    return list(
        MessageTicket.objects.filter(ticket=ticket, interne=False)
        .select_related("auteur")
        .prefetch_related("pieces__fichier")
        .order_by("cree_le")
    )


def fil_agent(ticket: Ticket) -> list:
    return list(
        MessageTicket.objects.filter(ticket=ticket)
        .select_related("auteur")
        .prefetch_related("pieces__fichier")
        .order_by("cree_le")
    )


def etat_engagement(ticket: Ticket, maintenant=None) -> dict:
    """Le compteur d'engagement à cet instant, pour l'écran de l'agent."""
    maintenant = maintenant or timezone.now()
    debut = (ticket.engagement_depuis or maintenant).date()
    feries = engagement.feries_entre(debut, maintenant.date())
    regle = engagement.engagement(ticket)
    consomme = engagement.consomme(ticket, maintenant, feries)
    return {
        "delai_min": regle.delai_min,
        "continu": regle.continu,
        "niveau": regle.niveau,
        "consomme_min": consomme // 60,
        "arrete": ticket.engagement_depuis is None,
        "en_retard": engagement.en_retard(ticket, maintenant, feries),
    }
