"""Couche d'abstraction des passerelles.

Le cœur applicatif ne connaît ni SMPP ni Jasmin : il connaît ce contrat. Chaque
adaptateur traduit les libellés de statut de son opérateur vers les états
internes et déclare sa propre table de correspondance. Le contrat complet,
débit et limites compris, est l'objet de T-19 ; celui-ci en est le noyau.
"""

import random
import secrets
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.utils import timezone

from .models import Connecteur

# États internes, en chaînes : `telecom` ne dépend pas de `journal`.
LIVRE, NON_LIVRE, EXPIRE, REJETE = "LIVRE", "NON_LIVRE", "EXPIRE", "REJETE"


@dataclass(frozen=True)
class Soumission:
    numero: str
    texte: str
    expediteur: str


@dataclass(frozen=True)
class Accuse:
    connecteur_id: object
    reference_smsc: str
    libelle_operateur: str
    etat: str
    horodatage: datetime


class Passerelle(ABC):
    """Ce que tout adaptateur doit savoir faire."""

    correspondance: dict[str, str] = {}

    def __init__(self, connecteur: Connecteur):
        self.connecteur = connecteur

    @abstractmethod
    def soumettre(self, soumission: Soumission) -> str:
        """Soumet un message et renvoie l'identifiant rendu par la passerelle."""

    @abstractmethod
    def accuses_dus(self, maintenant: datetime | None = None) -> list[Accuse]:
        """Accusés arrivés depuis le dernier appel."""

    def traduire(self, libelle: str) -> str:
        return self.correspondance.get(libelle.upper(), NON_LIVRE)


@dataclass
class _EnAttente:
    reference: str
    libelle: str
    du_le: datetime


@dataclass
class Simulateur(Passerelle):
    """Connecteur simulé : accepte tout, rend un identifiant hexadécimal comme
    certains SMSC, et produit un accusé après un délai réglable, avec un taux
    d'échec paramétrable. Réglages lus dans `connecteur.parametres`."""

    connecteur: Connecteur
    horloge: Callable[[], datetime] = timezone.now
    correspondance: dict[str, str] = field(
        default_factory=lambda: {
            "DELIVRD": LIVRE,
            "UNDELIV": NON_LIVRE,
            "EXPIRED": EXPIRE,
            "REJECTD": REJETE,
        }
    )

    def __post_init__(self):
        parametres = self.connecteur.parametres or {}
        self.delai = timedelta(seconds=float(parametres.get("delai_accuse_s", 0)))
        self.taux_echec = float(parametres.get("taux_echec", 0))
        self.aleatoire = random.Random(parametres.get("graine"))
        self.en_attente: list[_EnAttente] = []
        self.soumissions: list[tuple[str, Soumission]] = []

    def soumettre(self, soumission: Soumission) -> str:
        reference = secrets.token_hex(8)
        libelle = "UNDELIV" if self.aleatoire.random() < self.taux_echec else "DELIVRD"
        self.en_attente.append(_EnAttente(reference, libelle, self.horloge() + self.delai))
        self.soumissions.append((reference, soumission))
        return reference

    def accuses_dus(self, maintenant: datetime | None = None) -> list[Accuse]:
        maintenant = maintenant or self.horloge()
        dus = [e for e in self.en_attente if e.du_le <= maintenant]
        self.en_attente = [e for e in self.en_attente if e.du_le > maintenant]
        return [
            Accuse(self.connecteur.id, e.reference, e.libelle, self.traduire(e.libelle), e.du_le)
            for e in dus
        ]


ADAPTATEURS: dict[str, type[Passerelle]] = {Connecteur.Type.SIMULE: Simulateur}


def passerelle_pour(connecteur: Connecteur, **options) -> Passerelle:
    try:
        adaptateur = ADAPTATEURS[connecteur.type]
    except KeyError:
        raise NotImplementedError(f"Aucun adaptateur pour le type {connecteur.type}") from None
    return adaptateur(connecteur, **options)
