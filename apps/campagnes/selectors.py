"""Lectures des campagnes."""

from dataclasses import dataclass

from django.db.models import Count, Q

from apps.core.etats import Etat
from apps.journal.models import Destinataire as Servi
from apps.journal.models import Message
from apps.journal.services import NON_SOUMIS

from .models import Campagne, Destinataire


@dataclass(frozen=True)
class Bilan:
    # Partis vers le réseau : ils ne se rattrapent pas.
    partis: int
    # Soumission commencée, sort pas encore écrit.
    en_soumission: int
    # Pas encore soumis : retenus, en file, ou pas encore publiés.
    non_soumis: int
    annules: int
    # Refusés avant le réseau (liste noire, expéditeur), sans frais.
    refuses: int


def bilan(campagne: Campagne) -> Bilan | None:
    """Où en sont les messages d'une campagne confirmée. Lu à l'affichage d'une
    campagne, jamais rafraîchi en boucle : le suivi en direct lira les
    compteurs pré-agrégés (T-67). Borné aux partitions de la campagne et porté
    par l'index (campagne_id, etat)."""
    if not campagne.confirmee_le:
        return None
    comptes = Message.objects.filter(
        campagne_id=campagne.id, date_reception__gte=campagne.confirmee_le
    ).aggregate(
        partis=Count("id", filter=Q(date_soumission__isnull=False)),
        en_soumission=Count(
            "id", filter=Q(etat__in=NON_SOUMIS, soumission_debutee_le__isnull=False)
        ),
        en_attente=Count("id", filter=Q(etat__in=NON_SOUMIS, soumission_debutee_le__isnull=True)),
        annules=Count("id", filter=Q(etat=Etat.ANNULE)),
        refuses=Count("id", filter=Q(etat=Etat.REJETE, date_soumission__isnull=True)),
    )
    non_publies = (
        Destinataire.objects.filter(campagne=campagne).count()
        - Servi.objects.filter(campagne_id=campagne.id).count()
    )
    annulee = campagne.etat == Campagne.Etat.ANNULEE
    return Bilan(
        partis=comptes["partis"],
        en_soumission=comptes["en_soumission"],
        non_soumis=comptes["en_attente"] + (0 if annulee else non_publies),
        annules=comptes["annules"] + (non_publies if annulee else 0),
        refuses=comptes["refuses"],
    )
