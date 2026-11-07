"""Envois programmés et récurrents, à chaque tour de l'ordonnanceur (T-65).

L'ordonnanceur n'a qu'un exemplaire actif, protégé par un verrou consultatif
PostgreSQL. La création d'une occurrence est de plus protégée par la contrainte
d'unicité (campagne récurrente, créneau) : même deux tours concurrents ne
créent qu'une occurrence par créneau.

Chaque campagne est traitée dans sa propre transaction, et l'échec de l'une
n'arrête ni les autres ni le reste du tour de l'ordonnanceur.
"""

import logging
from datetime import datetime, timedelta

from django.db import transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.core.audit import auditer
from apps.core.evenements import publier

from . import services
from .models import Campagne

journal = logging.getLogger(__name__)


def liberer_programmees(maintenant: datetime) -> int:
    """Les campagnes programmées dont l'heure est venue passent EN_ATTENTE :
    l'ordonnanceur les publie dès ce tour. Une campagne annulée entretemps
    n'est plus PLANIFIEE et n'est pas touchée. Celles d'un compte suspendu
    attendent sa réactivation (T-95)."""
    return Campagne.objects.filter(
        etat=Campagne.Etat.PLANIFIEE,
        recurrence_jours__isnull=True,
        planifiee_pour__lte=maintenant,
        client__etat=Client.Etat.ACTIF,
    ).update(etat=Campagne.Etat.EN_ATTENTE, modifie_le=maintenant)


@transaction.atomic
def declencher_occurrence(patron_id, maintenant: datetime) -> Campagne | None:
    """Crée l'occurrence échue d'une campagne récurrente et la confirme si son
    coût reste sous le plafond ; sinon elle attend la confirmation du client,
    et un événement le signale. Avance la prochaine occurrence, et termine la
    récurrence après sa date de fin. Renvoie l'occurrence créée, ou None."""
    patron = (
        Campagne.objects.select_for_update(of=("self",))
        .select_related("client", "expediteur")
        .get(id=patron_id)
    )
    if patron.etat != Campagne.Etat.PLANIFIEE or not patron.recurrente:
        return None
    if patron.prochaine_occurrence is None or patron.prochaine_occurrence > maintenant:
        return None
    creneau, sautes = services.prochain_creneau(patron, maintenant)
    if patron.recurrence_fin and creneau > patron.recurrence_fin:
        _terminer(patron, maintenant)
        return None
    if sautes:
        journal.warning(
            "Campagne récurrente %s : %s créneau(x) sauté(s) pendant un arrêt, "
            "une seule occurrence part.",
            patron.id,
            sautes,
            extra={"campagne_id": str(patron.id)},
        )
    occurrence, creee = services.creer_occurrence(patron, creneau)
    patron.prochaine_occurrence = creneau + timedelta(days=patron.recurrence_jours)
    patron.save(update_fields=["prochaine_occurrence", "modifie_le"])
    if patron.recurrence_fin and patron.prochaine_occurrence > patron.recurrence_fin:
        _terminer(patron, maintenant)
    if not creee:
        return None
    attente = services.confirmer_occurrence(occurrence, patron.plafond_occurrence)
    if attente:
        publier(
            "campagne.occurrence.a_confirmer",
            client_id=patron.client_id,
            objet_id=occurrence.id,
            donnees={"campagne_recurrente": str(patron.id), "motif": attente},
            cle_idempotence=f"occurrence-a-confirmer:{occurrence.id}",
        )
    auditer(
        "campagnes.occurrence",
        occurrence,
        client=patron.client,
        apres={"creneau": creneau.isoformat(), "en_attente": attente or ""},
    )
    return occurrence


def _terminer(patron: Campagne, maintenant: datetime) -> None:
    patron.etat = Campagne.Etat.TERMINEE
    patron.terminee_le = maintenant
    patron.save(update_fields=["etat", "terminee_le", "modifie_le"])


def recurrentes_echues(maintenant: datetime) -> list:
    return list(
        Campagne.objects.filter(
            etat=Campagne.Etat.PLANIFIEE,
            recurrence_jours__isnull=False,
            parent__isnull=True,
            prochaine_occurrence__lte=maintenant,
            client__etat=Client.Etat.ACTIF,
        ).values_list("id", flat=True)
    )


def planifier(maintenant: datetime | None = None) -> int:
    """Un tour de planification : campagnes programmées libérées, occurrences
    échues créées. Renvoie le nombre de campagnes mises en route."""
    maintenant = maintenant or timezone.now()
    lancees = liberer_programmees(maintenant)
    for patron_id in recurrentes_echues(maintenant):
        try:
            if declencher_occurrence(patron_id, maintenant):
                lancees += 1
        except Exception:
            journal.exception(
                "Occurrence de la campagne récurrente %s en échec.",
                patron_id,
                extra={"campagne_id": str(patron_id)},
            )
    return lancees
