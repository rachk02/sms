"""Contrôle de l'expéditeur à la soumission, et politique de campagne (T-60).

Un message ne part sous un identifiant alphanumérique que si cet identifiant
appartient au client émetteur et qu'il est APPROUVÉ sur le réseau qui porte le
numéro visé, sans être suspendu. Le numéro court de la plateforme est toujours
admis. Le contrôle est enregistré auprès de `apps.core.controles` : tout chemin
d'envoi le passe, juste avant la passerelle.

Une campagne choisit, pour les réseaux que son identifiant ne couvre pas,
l'une des trois politiques : refuser ces messages, les faire partir sous le
numéro court (la valeur par défaut), ou les mettre en attente jusqu'à
l'approbation. `choisir` rend la décision pour un réseau ; la campagne
l'applique à la préparation, puis de nouveau juste avant chaque soumission, car
un identifiant peut être révoqué pendant une diffusion.
"""

from dataclasses import dataclass
from enum import StrEnum

from apps.telecom.numero_court import numero_court

from .models import Expediteur, ExpediteurReseau

MOTIF_EXPEDITEUR = "EXPEDITEUR_NON_APPROUVE"


class Politique(StrEnum):
    REFUSER = "REFUSER"
    REPLI = "REPLI"
    ATTENDRE = "ATTENDRE"


class Action(StrEnum):
    ENVOYER = "ENVOYER"
    REPLI = "REPLI"
    REFUSER = "REFUSER"
    ATTENDRE = "ATTENDRE"


@dataclass(frozen=True)
class Choix:
    action: Action
    # L'expéditeur sous lequel le message part ; vide pour un refus.
    expediteur: str


def couvert(client_id, libelle: str, operateur: str) -> bool:
    if libelle == numero_court():
        return True
    return (
        ExpediteurReseau.objects.filter(
            client_id=client_id,
            expediteur__libelle=libelle,
            operateur=operateur,
            etat=ExpediteurReseau.Etat.APPROUVE,
        )
        .exclude(expediteur__etat=Expediteur.Etat.SUSPENDU)
        .exists()
    )


def choisir(client_id, libelle: str, operateur: str, politique: str) -> Choix:
    if couvert(client_id, libelle, operateur):
        return Choix(Action.ENVOYER, libelle)
    politique = Politique(politique)
    if politique == Politique.REPLI:
        return Choix(Action.REPLI, numero_court())
    if politique == Politique.ATTENDRE:
        return Choix(Action.ATTENDRE, libelle)
    return Choix(Action.REFUSER, "")


def controle_expediteur(message) -> str | None:
    if couvert(message.client_id, message.expediteur_libelle, message.operateur):
        return None
    return MOTIF_EXPEDITEUR
