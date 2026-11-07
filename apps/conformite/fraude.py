"""Détection de trafic artificiel (T-100) : alertes et blocage.

Trois règles, dont les seuils vivent en base (`RegleFraude`) :

- numéros contigus dans une campagne, vérifiés à sa confirmation
  (`campagnes.services`) ;
- pic de codes envoyés hors campagne vers une plage où le client n'envoyait
  pas, compté à l'enregistrement du message (`journal.fraude`) ;
- taux de livraison anormalement bas sur une plage, compté à chaque accusé
  (`journal.fraude`).

Une plage est un bloc de dix mille numéros : le numéro sans ses quatre
derniers chiffres. Au seuil d'alerte, une alerte est ouverte et un événement
publié pour prévenir l'exploitation ; rien n'est bloqué. Au seuil critique,
l'alerte devient critique : la campagne est refusée, ou les envois du client
vers la plage sont refusés avant le réseau, sans frais, jusqu'à ce qu'un agent
traite l'alerte.
"""

import logging

from django.db import transaction

from apps.core.cloisonnement import client_pose, poser_client
from apps.core.evenements import publier

from .models import AlerteFraude, RegleFraude

journal = logging.getLogger(__name__)

CHIFFRES_DE_PLAGE = 4
MOTIF_PLAGE_BLOQUEE = "PLAGE_BLOQUEE"


def plage(numero: str) -> str:
    return numero[:-CHIFFRES_DE_PLAGE] if len(numero) > CHIFFRES_DE_PLAGE + 1 else numero


def regle(code: str) -> RegleFraude | None:
    return RegleFraude.objects.filter(code=code, actif=True).first()


def signaler(
    client_id,
    code: str,
    *,
    gravite: str,
    valeur: int,
    plage: str = "",
    campagne_id=None,
    details: dict | None = None,
) -> AlerteFraude:
    """Ouvre l'alerte, ou met à jour celle qui est ouverte pour le même client,
    la même règle, la même plage et la même campagne. Une alerte critique le
    reste. L'exploitation est prévenue à l'ouverture et au passage en critique,
    pas à chaque mise à jour. Le client est posé le temps de l'écriture :
    l'appelant peut être hors de la transaction d'une requête."""
    with transaction.atomic():
        precedent = client_pose()
        poser_client(client_id)
        alerte = (
            AlerteFraude.objects.select_for_update()
            .filter(
                client_id=client_id,
                regle=code,
                plage=plage,
                campagne_id=campagne_id,
                traitee_le__isnull=True,
            )
            .first()
        )
        prevenir = alerte is None
        if alerte is None:
            alerte = AlerteFraude(
                client_id=client_id, regle=code, plage=plage, campagne_id=campagne_id
            )
            alerte.gravite = gravite
        elif gravite == AlerteFraude.Gravite.CRITIQUE and alerte.gravite != gravite:
            alerte.gravite = gravite
            prevenir = True
        alerte.valeur = valeur
        alerte.details = details or {}
        alerte.save()
        if prevenir:
            publier(
                "conformite.fraude.alerte",
                client_id=client_id,
                objet_id=alerte.id,
                donnees={
                    "regle": code,
                    "gravite": alerte.gravite,
                    "plage": plage,
                    "campagne_id": str(campagne_id) if campagne_id else None,
                    "valeur": valeur,
                    "details": alerte.details,
                },
            )
            journal.warning(
                "Alerte anti-fraude %s (%s) pour le client %s : %s.",
                code,
                alerte.gravite,
                client_id,
                valeur,
                extra={"client_id": str(client_id), "alerte_id": str(alerte.id)},
            )
        poser_client(precedent or None)
    return alerte


def gravite(valeur: int, regle: RegleFraude, *, bas: bool = False) -> str | None:
    """La gravité atteinte par une mesure, ou None sous le seuil d'alerte.
    `bas` : la règle alerte quand la mesure descend (taux de livraison)."""
    if bas:
        if valeur < regle.seuil_critique:
            return AlerteFraude.Gravite.CRITIQUE
        return AlerteFraude.Gravite.ALERTE if valeur < regle.seuil_alerte else None
    if valeur >= regle.seuil_critique:
        return AlerteFraude.Gravite.CRITIQUE
    return AlerteFraude.Gravite.ALERTE if valeur >= regle.seuil_alerte else None


def controle_plage_bloquee(message) -> str | None:
    """Contrôle avant soumission : un message du client vers une plage sous
    alerte critique non traitée ne part pas. Un message système part."""
    if message.nature == "SYSTEME":
        return None
    bloquee = AlerteFraude.objects.filter(
        client_id=message.client_id,
        plage=plage(message.numero_normalise),
        gravite=AlerteFraude.Gravite.CRITIQUE,
        traitee_le__isnull=True,
    ).exists()
    return MOTIF_PLAGE_BLOQUEE if bloquee else None
