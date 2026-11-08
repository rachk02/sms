"""Compteur d'engagement de service (T-102).

Chaque catégorie fixe sa priorité et son délai de première réponse. Les délais
s'entendent en heures ouvrées, du lundi au vendredi de 8 à 18 heures et le
samedi matin, hors jours fériés, en heure de Ouagadougou ; la P1 et la
réquisition courent en continu. Le compteur s'arrête quand le ticket attend le
client ou l'opérateur, et quand il est résolu : l'engagement mesure notre
lenteur, pas celle des autres.

Les fonctions sont pures : l'instant et les jours fériés se passent en
paramètres.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.conf import settings

from .models import Ticket

C = Ticket.Categorie
P = Ticket.Priorite
E = Ticket.Etat

# Heures ouvrées par jour de la semaine (lundi = 0). Le samedi s'arrête à midi,
# à confirmer avec l'utilisateur.
HORAIRES = {jour: (time(8), time(18)) for jour in range(5)} | {5: (time(8), time(12))}
HEURES_PAR_JOUR = 10


@dataclass(frozen=True)
class Engagement:
    priorite: str
    delai_min: int
    continu: bool
    niveau: str


# Délais de première réponse, en minutes. Un jour vaut une journée ouvrée de
# dix heures. « Immédiat », pour la réquisition, est ramené à quinze minutes :
# à confirmer avec l'utilisateur.
ENGAGEMENTS = {
    C.SERVICE_INDISPONIBLE: Engagement(P.P1, 60, True, "2"),
    C.ACHEMINEMENT: Engagement(P.P1, 60, True, "2"),
    C.REQUISITION: Engagement(P.P1, 15, True, "Direction"),
    C.ACCES_BLOQUE: Engagement(P.P2, 4 * 60, False, "1"),
    C.PAIEMENT: Engagement(P.P2, 4 * 60, False, "1"),
    C.EXPEDITEUR: Engagement(P.P2, HEURES_PAR_JOUR * 60, False, "1"),
    C.RECLAMATION: Engagement(P.P2, HEURES_PAR_JOUR * 60, False, "1 et conformité"),
    C.NON_LIVRE: Engagement(P.P3, HEURES_PAR_JOUR * 60, False, "1 puis 2"),
    C.ONBOARDING: Engagement(P.P3, HEURES_PAR_JOUR * 60, False, "1"),
    C.INTEGRATION: Engagement(P.P3, HEURES_PAR_JOUR * 60, False, "2"),
    C.FACTURATION: Engagement(P.P3, 2 * HEURES_PAR_JOUR * 60, False, "1"),
    C.USAGE: Engagement(P.P4, 2 * HEURES_PAR_JOUR * 60, False, "1"),
}

# États où le compteur court ; ailleurs il est arrêté.
EN_COURS = frozenset({E.NOUVEAU, E.PRIS_EN_CHARGE, E.ESCALADE_N2})


def _fuseau() -> ZoneInfo:
    return ZoneInfo(settings.TIME_ZONE)


def secondes_ouvrees(
    debut: datetime, fin: datetime, *, continu: bool = False, feries: frozenset = frozenset()
) -> int:
    """Les secondes entre deux instants, en heures ouvrées, ou en continu."""
    if fin <= debut:
        return 0
    if continu:
        return int((fin - debut).total_seconds())
    fuseau = _fuseau()
    jour, dernier = debut.astimezone(fuseau).date(), fin.astimezone(fuseau).date()
    total = 0.0
    while jour <= dernier:
        plage = HORAIRES.get(jour.weekday())
        if plage and jour not in feries:
            ouverture = datetime.combine(jour, plage[0], fuseau)
            fermeture = datetime.combine(jour, plage[1], fuseau)
            recouvrement = min(fin, fermeture) - max(debut, ouverture)
            total += max(recouvrement.total_seconds(), 0)
        jour += timedelta(days=1)
    return int(total)


def engagement(ticket: Ticket) -> Engagement:
    return ENGAGEMENTS[ticket.categorie]


def consomme(ticket: Ticket, maintenant: datetime, feries: frozenset = frozenset()) -> int:
    """Les secondes d'engagement consommées à cet instant."""
    en_cours = 0
    if ticket.engagement_depuis:
        en_cours = secondes_ouvrees(
            ticket.engagement_depuis,
            maintenant,
            continu=engagement(ticket).continu,
            feries=feries,
        )
    return ticket.engagement_consomme_s + en_cours


def en_retard(ticket: Ticket, maintenant: datetime, feries: frozenset = frozenset()) -> bool:
    """Sans première réponse au-delà du délai de sa catégorie."""
    return (
        ticket.premiere_reponse_le is None
        and consomme(ticket, maintenant, feries) > engagement(ticket).delai_min * 60
    )


def arreter(ticket: Ticket, maintenant: datetime, feries: frozenset = frozenset()) -> None:
    """Fige le compteur : ce qui a couru s'ajoute au consommé."""
    if ticket.engagement_depuis:
        ticket.engagement_consomme_s = consomme(ticket, maintenant, feries)
        ticket.engagement_depuis = None


def relancer(ticket: Ticket, maintenant: datetime) -> None:
    if ticket.engagement_depuis is None:
        ticket.engagement_depuis = maintenant


def feries_entre(debut: date, fin: date) -> frozenset:
    """Les jours fériés du pays entre deux dates, lus en base (lot 6)."""
    from apps.conformite.models import JourFerie

    return frozenset(
        JourFerie.objects.filter(pays="BF", date__range=(debut, fin)).values_list("date", flat=True)
    )
