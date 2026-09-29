"""Santé des connecteurs : trois signaux combinés.

- l'état de la session, que l'adaptateur constate en sondant sa passerelle ;
- le taux d'accusés positifs sur une fenêtre glissante de cinq minutes ;
- le délai moyen entre soumission et accusé sur la même fenêtre.

Une session tombée est un incident immédiat. Un taux qui s'effondre alors que
la session tient est plus insidieux : il signale souvent un problème côté
opérateur, ou un identifiant d'expéditeur qui vient d'être bloqué. La fenêtre
vit dans Redis, alimentée à chaque accusé appliqué.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.utils import timezone

from apps.core.evenements import publier
from apps.core.redis import client_redis

from .models import Connecteur
from .passerelles import EtatLiaison, passerelle_pour

journal = logging.getLogger(__name__)

FENETRE = timedelta(minutes=5)
SEUIL_TAUX = 0.85
# Hystérésis : une route dégradée ne redevient saine qu'à 90 %. Sans cet écart,
# une route qui tourne autour de 85 % alternerait alertes et rétablissements.
SEUIL_RETABLISSEMENT = 0.90
# En dessous, la fenêtre compte trop peu d'accusés pour conclure : une poignée
# d'échecs sur un trafic faible ne doit pas déclarer une route dégradée.
ECHANTILLON_MIN = 20
CLE = "sms:sante:{connecteur_id}:accuses"


@dataclass(frozen=True)
class Mesures:
    total: int
    positifs: int
    delai_moyen_s: float | None

    @property
    def taux(self) -> float | None:
        return self.positifs / self.total if self.total else None


@dataclass(frozen=True)
class Sante:
    connecteur_id: object
    session: EtatLiaison
    mesures: Mesures
    etat: EtatLiaison
    motif: str


def enregistrer_accuse(connecteur_id, positif: bool, delai_s: float | None, instant: datetime):
    """Verse un accusé dans la fenêtre glissante du connecteur."""
    cle = CLE.format(connecteur_id=connecteur_id)
    horodatage = instant.timestamp()
    membre = f"{int(positif)}:{'' if delai_s is None else round(delai_s, 3)}:{uuid.uuid4().hex}"
    redis = client_redis()
    with redis.pipeline() as lot:
        lot.zadd(cle, {membre: horodatage})
        lot.zremrangebyscore(cle, "-inf", horodatage - FENETRE.total_seconds())
        lot.expire(cle, int(FENETRE.total_seconds() * 2))
        lot.execute()


def mesures(connecteur_id, maintenant: datetime | None = None) -> Mesures:
    maintenant = maintenant or timezone.now()
    fin = maintenant.timestamp()
    membres = client_redis().zrangebyscore(
        CLE.format(connecteur_id=connecteur_id), fin - FENETRE.total_seconds(), fin
    )
    positifs, delais = 0, []
    for membre in membres:
        positif, delai, _ = membre.decode().split(":", 2)
        positifs += positif == "1"
        if delai:
            delais.append(float(delai))
    moyen = sum(delais) / len(delais) if delais else None
    return Mesures(total=len(membres), positifs=positifs, delai_moyen_s=moyen)


def evaluer(passerelle, maintenant: datetime | None = None) -> Sante:
    session = passerelle.sonder()
    releve = mesures(passerelle.connecteur.id, maintenant)
    deja_degradee = passerelle.connecteur.etat_liaison == EtatLiaison.DEGRADE
    seuil = SEUIL_RETABLISSEMENT if deja_degradee else SEUIL_TAUX
    if session == EtatLiaison.DECONNECTE:
        etat, motif = EtatLiaison.DECONNECTE, "session tombée"
    elif releve.total >= ECHANTILLON_MIN and releve.taux < seuil:
        etat = EtatLiaison.DEGRADE
        motif = (
            f"{releve.taux:.0%} d'accusés positifs sur cinq minutes, sous le seuil de "
            f"{seuil:.0%} ({releve.positifs} sur {releve.total})"
        )
    else:
        etat, motif = EtatLiaison.CONNECTE, "liaison saine"
    return Sante(passerelle.connecteur.id, session, releve, etat, motif)


EVENEMENTS = {
    EtatLiaison.DECONNECTE: "telecom.liaison.tombee",
    EtatLiaison.DEGRADE: "telecom.liaison.degradee",
    EtatLiaison.CONNECTE: "telecom.liaison.retablie",
}


def surveiller(maintenant: datetime | None = None) -> list[Sante]:
    """Évalue chaque connecteur actif, tient `etat_liaison` à jour et alerte à
    chaque changement : jamais d'alerte répétée tant que l'état ne bouge pas.
    Appelée par l'ordonnanceur, sous le rôle worker."""
    bilans = []
    for connecteur in Connecteur.objects.filter(actif=True):
        sante = evaluer(passerelle_pour(connecteur), maintenant)
        bilans.append(sante)
        if connecteur.etat_liaison == sante.etat:
            continue
        champs = {"etat_liaison": sante.etat}
        if sante.etat == EtatLiaison.CONNECTE:
            champs["derniere_connexion_le"] = timezone.now()
        Connecteur.objects.filter(id=connecteur.id).update(**champs)
        niveau = logging.INFO if sante.etat == EtatLiaison.CONNECTE else logging.ERROR
        journal.log(
            niveau,
            "Liaison %s : %s vers %s (%s).",
            connecteur.nom,
            connecteur.etat_liaison,
            sante.etat,
            sante.motif,
            extra={"connecteur_id": str(connecteur.id)},
        )
        publier(
            EVENEMENTS[sante.etat],
            objet_id=connecteur.id,
            donnees={
                "connecteur": connecteur.nom,
                "operateur": connecteur.operateur,
                "avant": connecteur.etat_liaison,
                "apres": sante.etat,
                "motif": sante.motif,
                "taux": sante.mesures.taux,
                "accuses": sante.mesures.total,
                "delai_moyen_s": sante.mesures.delai_moyen_s,
            },
        )
    return bilans
