"""Contrôle des compteurs pré-agrégés des campagnes terminées (T-91).

Les compteurs sont écrits dans la transaction de chaque changement d'état d'un
message : un worker qui tombe annule ensemble l'état et le compteur, et la
reprise les écrit ensemble, une seule fois. Rien ne devrait donc s'écarter.
Le contrôle le vérifie, campagne terminée par campagne terminée, en recomptant
le journal ; un écart se signale et ne se corrige que sur demande, par
`recompter`, pour qu'une anomalie ne disparaisse pas sans avoir été vue.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.utils import timezone

from apps.journal import selectors as journal_lectures
from apps.journal.services import recompter

from .models import Campagne

journal = logging.getLogger(__name__)

# Le journal est borné en haut aussi : un message de la campagne est reçu avant
# qu'elle se termine, la marge couvre l'écart d'horloge entre les processus.
MARGE_FIN = timedelta(hours=1)


@dataclass(frozen=True)
class Ecart:
    campagne_id: object
    client_id: object
    # {colonne: (compteurs, journal)}, seulement les colonnes qui diffèrent.
    colonnes: dict
    corrige: bool = False


def _comparer(campagne: Campagne) -> dict:
    """Le journal et les compteurs lus dans un même instantané. Une campagne
    terminée reçoit encore des accusés pendant des heures : lus l'un après
    l'autre, un accusé validé entre les deux lectures ferait un faux écart."""
    exterieur = connection.in_atomic_block
    with transaction.atomic():
        if not exterieur:
            with connection.cursor() as curseur:
                curseur.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
        attendu = journal_lectures.compte_journal(
            campagne.client_id,
            campagne.id,
            campagne.confirmee_le,
            campagne.terminee_le + MARGE_FIN,
        )
        lu = journal_lectures.compte_compteurs(campagne.client_id, campagne.id)
    return {c: (lu[c], attendu[c]) for c in attendu if lu[c] != attendu[c]}


def controler(campagne: Campagne, *, corriger: bool = False) -> Ecart | None:
    """Compare les compteurs d'une campagne terminée au journal. Renvoie l'écart
    trouvé, ou None quand tout concorde."""
    if not campagne.confirmee_le or not campagne.terminee_le:
        return None
    colonnes = _comparer(campagne)
    if not colonnes:
        return None
    if corriger:
        recompter(campagne.client_id, campagne.id, campagne.confirmee_le)
    return Ecart(campagne.id, campagne.client_id, colonnes, corrige=corriger)


def controler_terminees(
    maintenant: datetime | None = None, *, jours: int = 1, corriger: bool = False
) -> list[Ecart]:
    """Contrôle les campagnes terminées dans les `jours` derniers jours.
    L'ordonnanceur le fait une fois par jour, sans corriger."""
    maintenant = maintenant or timezone.now()
    campagnes = Campagne.objects.filter(
        terminee_le__gte=maintenant - timedelta(days=jours),
        terminee_le__lt=maintenant,
        confirmee_le__isnull=False,
    ).order_by("terminee_le", "id")
    ecarts = []
    # La liste d'abord : chaque contrôle ouvre sa propre transaction.
    for campagne in list(campagnes):
        ecart = controler(campagne, corriger=corriger)
        if ecart:
            journal.error(
                "Compteurs de la campagne %s différents du journal : %s%s.",
                campagne.id,
                ", ".join(f"{c} {a} au lieu de {b}" for c, (a, b) in ecart.colonnes.items()),
                ", recomptés" if ecart.corrige else "",
                extra={"campagne_id": str(campagne.id)},
            )
            ecarts.append(ecart)
    return ecarts
