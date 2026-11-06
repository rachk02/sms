"""Fenêtre légale des envois marketing (T-72) et jours fériés (T-73).

Un message marketing ne part qu'à l'intérieur de la fenêtre de son pays : le
jour de la semaine a une ligne dans `conformite_fenetre`, l'heure locale est
comprise entre l'ouverture (incluse) et la fermeture (exclue), et le jour n'est
pas férié. Un jour sans ligne est fermé ; si aucune fenêtre n'est configurée
pour un pays, rien n'y est jamais ouvert. Les messages transactionnels et les
codes à usage unique ne passent pas par ici.

Les deux tables sont relues à chaque question : ajouter un jour férié ou
changer une heure prend effet sans redémarrage.
"""

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .models import Fenetre, JourFerie

# Le fuseau de chaque pays servi ; Ouagadougou ne pratique pas l'heure d'été.
FUSEAUX = {"BF": ZoneInfo("Africa/Ouagadougou")}

# Au-delà, une fenêtre qui ne rouvre jamais n'est plus cherchée.
HORIZON_JOURS = 366


def _plages(pays: str) -> dict[int, tuple[time, time]]:
    return {
        jour: (ouverture, fermeture)
        for jour, ouverture, fermeture in Fenetre.objects.filter(pays=pays).values_list(
            "jour", "ouverture", "fermeture"
        )
    }


def _feries(pays: str, debut: date, fin: date) -> set[date]:
    return set(
        JourFerie.objects.filter(pays=pays, date__range=(debut, fin)).values_list("date", flat=True)
    )


def ouverte(instant: datetime, pays: str = "BF") -> bool:
    """Vrai si un message marketing peut partir à cet instant."""
    local = instant.astimezone(FUSEAUX[pays])
    plage = Fenetre.objects.filter(pays=pays, jour=local.isoweekday()).values_list(
        "ouverture", "fermeture"
    )
    plage = plage.first()
    if plage is None or not plage[0] <= local.time() < plage[1]:
        return False
    return not JourFerie.objects.filter(pays=pays, date=local.date()).exists()


def prochaine_ouverture(instant: datetime, pays: str = "BF") -> datetime | None:
    """L'instant où la fenêtre est ouverte, à partir de `instant` : lui-même si
    elle l'est déjà, sinon la prochaine ouverture, par-delà les jours fermés
    qui s'enchaînent (samedi soir, dimanche, lundi férié). None si la fenêtre
    ne rouvre pas dans l'année : rien n'est configuré, et l'écran le dit au lieu
    d'inventer une heure."""
    fuseau = FUSEAUX[pays]
    local = instant.astimezone(fuseau)
    plages = _plages(pays)
    if not plages:
        return None
    feries = _feries(pays, local.date(), local.date() + timedelta(days=HORIZON_JOURS))
    for decalage in range(HORIZON_JOURS + 1):
        jour = local.date() + timedelta(days=decalage)
        plage = plages.get(jour.isoweekday())
        if plage is None or jour in feries:
            continue
        ouverture = datetime.combine(jour, plage[0], tzinfo=fuseau)
        fermeture = datetime.combine(jour, plage[1], tzinfo=fuseau)
        if local < ouverture:
            return ouverture
        if local < fermeture:
            return instant
    return None
