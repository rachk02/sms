"""Couche d'abstraction des passerelles.

Le cœur applicatif ne connaît aucun protocole ni aucun fournisseur : il
connaît ce contrat. Un adaptateur par fournisseur l'implémente, déclare ses
limites et sa propre table de correspondance entre les libellés de statut de
son opérateur et les états internes. Changer de fournisseur, c'est écrire un
adaptateur, jamais toucher au code métier.
"""

import logging
import random
import secrets
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import ClassVar

from django.utils import timezone

from apps.core.encodage import Segment, decouper
from apps.core.etats import ETATS_D_ACCUSE, Etat

from .models import Connecteur

journal = logging.getLogger(__name__)

# État retenu pour un libellé qu'aucune table ne connaît : un échec, jamais une
# livraison supposée. La trace laissée permet de compléter la table.
ETAT_PAR_DEFAUT = Etat.NON_LIVRE


class EtatLiaison(StrEnum):
    CONNECTE = "CONNECTE"
    DEGRADE = "DEGRADE"
    DECONNECTE = "DECONNECTE"


class SortSoumission(StrEnum):
    """Ce qu'une passerelle sait d'une soumission, quand on l'interroge."""

    INCONNUE = "INCONNUE"  # jamais reçue : il faut soumettre
    ACCEPTEE = "ACCEPTEE"  # reçue, accusé pas encore arrivé
    TERMINEE = "TERMINEE"  # accusé final connu


class PasserelleIndisponible(Exception):
    """La liaison ne peut pas prendre de soumission maintenant. Le message reste
    à soumettre plus tard ; ce n'est pas un rejet."""


class SoumissionRejetee(Exception):
    """La passerelle a refusé le message lui-même. Réessayer n'y changera rien."""

    def __init__(self, code: str, motif: str):
        self.code = code
        self.motif = motif
        super().__init__(f"Soumission rejetée ({code}) : {motif}")


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


@dataclass(frozen=True)
class Limites:
    messages_par_seconde: int
    sessions: int
    fenetre: int


@dataclass(frozen=True)
class Statut:
    sort: SortSoumission
    reference_smsc: str | None = None
    etat: str | None = None


class Passerelle(ABC):
    """Ce que tout adaptateur doit savoir faire.

    `correspondance` associe chaque libellé de statut de l'opérateur à un état
    interne ; elle est vérifiée à la déclaration de la classe.
    """

    correspondance: ClassVar[dict[str, Etat]] = {}
    # Vrai si `statut` sait retrouver une soumission dont on n'a pas gardé la
    # référence. Faux : après une coupure, le sort d'une soumission est inconnu.
    retrouve_ses_soumissions: ClassVar[bool] = True

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        hors_accuse = {
            libelle: etat
            for libelle, etat in cls.correspondance.items()
            if etat not in ETATS_D_ACCUSE
        }
        if hors_accuse:
            raise TypeError(f"{cls.__name__} : états qu'un accusé ne peut annoncer {hors_accuse}")
        if any(libelle != libelle.upper() for libelle in cls.correspondance):
            raise TypeError(f"{cls.__name__} : libellés de correspondance attendus en majuscules")

    def __init__(self, connecteur: Connecteur):
        self.connecteur = connecteur
        self._indisponible: str | None = None

    @abstractmethod
    def soumettre(self, soumission: Soumission) -> str:
        """Soumet un message et renvoie l'identifiant rendu par la passerelle.

        Lève PasserelleIndisponible si la liaison ne peut pas le prendre, et
        SoumissionRejetee si le message lui-même est refusé.
        """

    @abstractmethod
    def statut(self, soumission: Soumission, reference_smsc: str | None = None) -> Statut:
        """Sort d'une soumission, pour la reprise après une coupure : faut-il
        soumettre, ou la passerelle l'a-t-elle déjà reçue ?"""

    @abstractmethod
    def accuses_dus(self, maintenant: datetime | None = None) -> list[Accuse]:
        """Accusés arrivés depuis le dernier appel."""

    @abstractmethod
    def etat(self) -> EtatLiaison:
        """État de la liaison, tel que l'adaptateur le constate."""

    def interpreter_rappel(self, donnees: dict) -> Accuse | None:
        """Accusé porté par un rappel que la passerelle adresse à la plateforme,
        pour les adaptateurs qui poussent leurs accusés au lieu de les laisser
        relever. None si le rappel n'annonce pas un état final."""
        raise NotImplementedError(f"{type(self).__name__} ne pousse pas ses accusés")

    def limites(self) -> Limites:
        return Limites(
            messages_par_seconde=self.connecteur.debit_max,
            sessions=self.connecteur.sessions_max,
            fenetre=self.connecteur.fenetre,
        )

    def declarer_indisponible(self, motif: str) -> None:
        """Retire la liaison du service : toute soumission est alors refusée
        comme indisponible, jusqu'à `declarer_disponible`."""
        self._indisponible = motif
        journal.warning("Liaison %s déclarée indisponible : %s", self.connecteur.nom, motif)

    def declarer_disponible(self) -> None:
        self._indisponible = None

    @property
    def disponible(self) -> bool:
        return self._indisponible is None and self.etat() != EtatLiaison.DECONNECTE

    def _exiger_disponible(self) -> None:
        if not self.disponible:
            raise PasserelleIndisponible(self._indisponible or f"liaison {self.etat()}")

    def traduire(self, libelle: str) -> Etat:
        etat = self.correspondance.get(libelle.strip().upper())
        if etat is None:
            journal.warning(
                "Libellé de statut inconnu %r sur %s, retenu comme %s.",
                libelle,
                self.connecteur.nom,
                ETAT_PAR_DEFAUT,
                extra={"connecteur_id": str(self.connecteur.id)},
            )
            return ETAT_PAR_DEFAUT
        return etat


@dataclass
class _EnAttente:
    reference: str
    soumission: Soumission
    libelle: str
    du_le: datetime


class Simulateur(Passerelle):
    """Connecteur simulé : accepte tout, rend un identifiant hexadécimal comme
    certains SMSC, et produit un accusé après un délai réglable, avec un taux
    d'échec paramétrable. Réglages lus dans `connecteur.parametres` :
    `delai_accuse_s`, `taux_echec`, `graine`, et `libelles` pour remplacer les
    libellés d'accusé par ceux d'un autre opérateur."""

    correspondance: ClassVar[dict[str, Etat]] = {
        "DELIVRD": Etat.LIVRE,
        "UNDELIV": Etat.NON_LIVRE,
        "EXPIRED": Etat.EXPIRE,
        "REJECTD": Etat.REJETE,
    }

    def __init__(self, connecteur: Connecteur, horloge: Callable[[], datetime] = timezone.now):
        super().__init__(connecteur)
        parametres = connecteur.parametres or {}
        self.horloge = horloge
        self.delai = timedelta(seconds=float(parametres.get("delai_accuse_s", 0)))
        self.taux_echec = float(parametres.get("taux_echec", 0))
        self.aleatoire = random.Random(parametres.get("graine"))
        libelles = parametres.get("libelles", {})
        self.libelle_livre = libelles.get("livre", "DELIVRD")
        self.libelle_echec = libelles.get("echec", "UNDELIV")
        self.en_attente: list[_EnAttente] = []
        self.soumissions: dict[str, Soumission] = {}
        self.segments: dict[str, list[Segment]] = {}
        self.accuses_rendus: dict[str, str] = {}

    def etat(self) -> EtatLiaison:
        return EtatLiaison.CONNECTE

    def soumettre(self, soumission: Soumission) -> str:
        self._exiger_disponible()
        reference = secrets.token_hex(8)
        echec = self.aleatoire.random() < self.taux_echec
        libelle = self.libelle_echec if echec else self.libelle_livre
        self.en_attente.append(
            _EnAttente(reference, soumission, libelle, self.horloge() + self.delai)
        )
        self.soumissions[reference] = soumission
        # Ce que le réseau recevrait : les segments et leur en-tête de concaténation.
        self.segments[reference] = decouper(soumission.texte, self.aleatoire.randrange(256))
        return reference

    def statut(self, soumission: Soumission, reference_smsc: str | None = None) -> Statut:
        if reference_smsc is None:
            reference_smsc = next(
                (ref for ref, connue in self.soumissions.items() if connue == soumission), None
            )
        if reference_smsc not in self.soumissions:
            return Statut(SortSoumission.INCONNUE)
        if reference_smsc in self.accuses_rendus:
            libelle = self.accuses_rendus[reference_smsc]
            return Statut(SortSoumission.TERMINEE, reference_smsc, self.traduire(libelle))
        return Statut(SortSoumission.ACCEPTEE, reference_smsc)

    def accuses_dus(self, maintenant: datetime | None = None) -> list[Accuse]:
        maintenant = maintenant or self.horloge()
        dus = [e for e in self.en_attente if e.du_le <= maintenant]
        self.en_attente = [e for e in self.en_attente if e.du_le > maintenant]
        for attente in dus:
            self.accuses_rendus[attente.reference] = attente.libelle
        return [
            Accuse(self.connecteur.id, e.reference, e.libelle, self.traduire(e.libelle), e.du_le)
            for e in dus
        ]


def _adaptateurs() -> dict[str, type[Passerelle]]:
    from .jasmin import Jasmin

    return {Connecteur.Type.SIMULE: Simulateur, Connecteur.Type.SMPP: Jasmin}


def passerelle_pour(connecteur: Connecteur, **options) -> Passerelle:
    """L'adaptateur du connecteur, choisi par sa configuration, jamais par le
    code métier. Une liaison SMPP passe par Jasmin."""
    try:
        adaptateur = _adaptateurs()[connecteur.type]
    except KeyError:
        raise NotImplementedError(f"Aucun adaptateur pour le type {connecteur.type}") from None
    return adaptateur(connecteur, **options)
