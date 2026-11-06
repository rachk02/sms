"""Porte d'écriture du module credits.

Toute opération sur le solde prend d'abord le verrou de la ligne de cache du
client (`credits_compte`, SELECT ... FOR UPDATE), puis celui de la réservation
concernée, toujours dans cet ordre : deux opérations concurrentes sur un même
client s'attendent au lieu de se croiser, et deux clients ne se gênent pas.

Une réservation, demandée par un client qui attend la réponse, n'attend le
verrou que `ATTENTE_RESERVATION_MS` : au-delà, elle est refusée par
SoldeOccupe plutôt que de laisser l'écran tourner. Les autres opérations
(achat, règlement, libération) viennent des workers et attendent leur tour.
"""

from dataclasses import dataclass

from django.db import OperationalError, connection, transaction
from django.db.models import Sum
from django.utils import timezone

from apps.comptes.models import Client
from apps.core.etats import Etat

from .models import CompteCredit, Ecriture, Reservation


class SoldeInsuffisant(Exception):
    def __init__(self, disponible: int, demande: int):
        self.disponible = disponible
        self.demande = demande
        super().__init__(f"Solde insuffisant : {disponible} FCFA disponibles, {demande} demandés.")


class SoldeOccupe(Exception):
    """Le verrou du solde n'a pas été obtenu dans le délai : une autre
    opération du même client le tient encore. Rien n'a été réservé."""

    def __init__(self):
        super().__init__(
            "Le solde est occupé par une autre opération en cours. Réessayez dans un instant."
        )


class MotifObligatoire(ValueError):
    pass


class ReferenceObligatoire(ValueError):
    pass


class ReservationClose(Exception):
    pass


class SansReservation(ReservationClose):
    """Message envoyé sans réservation : l'envoi unitaire de l'API, facturé
    avec les modèles de règlement du lot 7."""


@dataclass(frozen=True)
class Solde:
    acquis: int
    reserve: int

    @property
    def disponible(self) -> int:
        return self.acquis - self.reserve


def _montant_positif(montant: int) -> None:
    if not isinstance(montant, int) or montant <= 0:
        raise ValueError(f"Le montant doit être un entier positif de FCFA : {montant!r}")


ATTENTE_RESERVATION_MS = 10_000
VERROU_INDISPONIBLE = "55P03"


def _compte_verrouille(client_id, attente_max_ms: int | None = None) -> CompteCredit:
    """Verrouille la ligne de cache du client. Avec `attente_max_ms`, l'attente
    du verrou est bornée (lock_timeout local, rendu à sa valeur ensuite) et son
    dépassement lève SoldeOccupe."""
    CompteCredit.objects.bulk_create([CompteCredit(client_id=client_id)], ignore_conflicts=True)
    if attente_max_ms is None:
        return CompteCredit.objects.select_for_update().get(client_id=client_id)
    with connection.cursor() as curseur:
        curseur.execute(
            "SELECT current_setting('lock_timeout'), set_config('lock_timeout', %s, true)",
            [f"{attente_max_ms}ms"],
        )
        avant = curseur.fetchone()[0]
    try:
        with transaction.atomic():
            return CompteCredit.objects.select_for_update().get(client_id=client_id)
    except OperationalError as erreur:
        if getattr(erreur.__cause__, "sqlstate", None) == VERROU_INDISPONIBLE:
            raise SoldeOccupe() from None
        raise
    finally:
        with connection.cursor() as curseur:
            curseur.execute("SELECT set_config('lock_timeout', %s, true)", [avant])


def _ecrire(compte: CompteCredit, type_, montant, *, reference="", motif="", auteur=None):
    compte.solde_acquis += montant
    ecriture = Ecriture(
        client_id=compte.client_id,
        type=type_,
        montant=montant,
        solde_apres=compte.solde_acquis,
        reference=reference,
        motif=motif,
        auteur=auteur,
    )
    ecriture.save()
    compte.save(update_fields=["solde_acquis", "montant_reserve", "mis_a_jour_le"])
    return ecriture


@transaction.atomic
def acheter(client_id, montant: int, *, reference: str, auteur=None) -> Ecriture:
    """Achat constaté : paiement confirmé de serveur à serveur, ou paiement manuel
    validé par un agent, qui en est alors l'auteur. ACHAT ne désigne que des
    crédits payés : un crédit offert est un recrédit, jamais un achat."""
    _montant_positif(montant)
    if not reference or not reference.strip():
        raise ReferenceObligatoire("Un achat renvoie à sa transaction de paiement.")
    compte = _compte_verrouille(client_id)
    return _ecrire(compte, Ecriture.Type.ACHAT, montant, reference=reference.strip(), auteur=auteur)


@transaction.atomic
def recrediter(client_id, montant: int, *, motif: str = "", reference: str = "", auteur=None):
    """Recrédit, crédit offert : défaillance imputable à la plateforme, ou geste
    commercial. Automatique, il renvoie au message ou à l'incident par sa
    référence ; manuel, il porte son agent et son motif."""
    _montant_positif(montant)
    motif, reference = (motif or "").strip(), (reference or "").strip()
    if auteur is not None and not motif:
        raise MotifObligatoire("Un recrédit manuel exige un motif.")
    if auteur is None and not reference:
        raise ReferenceObligatoire(
            "Un recrédit automatique renvoie au message ou à l'incident qui le justifie."
        )
    compte = _compte_verrouille(client_id)
    return _ecrire(
        compte, Ecriture.Type.RECREDIT, montant, reference=reference, motif=motif, auteur=auteur
    )


@transaction.atomic
def ajuster(client_id, montant: int, *, motif: str, auteur, reference: str = "") -> Ecriture:
    """Correction après enquête, positive ou négative. Motif et auteur obligatoires."""
    if not motif or not motif.strip():
        raise MotifObligatoire("Un ajustement exige un motif.")
    if auteur is None:
        raise MotifObligatoire("Un ajustement exige un auteur.")
    if not isinstance(montant, int) or montant == 0:
        raise ValueError(f"Le montant d'un ajustement est un entier non nul : {montant!r}")
    compte = _compte_verrouille(client_id)
    return _ecrire(
        compte,
        Ecriture.Type.AJUSTEMENT,
        montant,
        reference=reference,
        motif=motif.strip(),
        auteur=auteur,
    )


@transaction.atomic
def reserver(
    client_id,
    montant: int,
    *,
    campagne_id=None,
    message_id=None,
    message_date_reception=None,
) -> Reservation:
    """Retient `montant` sur le solde disponible, pour une campagne entière ou un
    envoi unitaire. Lève SoldeInsuffisant si le solde ne couvre pas tout,
    SoldeOccupe si le verrou du solde reste pris au-delà du délai d'attente."""
    _montant_positif(montant)
    compte = _compte_verrouille(client_id, ATTENTE_RESERVATION_MS)
    if compte.solde_disponible < montant:
        raise SoldeInsuffisant(compte.solde_disponible, montant)
    compte.montant_reserve += montant
    compte.save(update_fields=["montant_reserve", "mis_a_jour_le"])
    return Reservation.objects.create(
        client_id=client_id,
        campagne_id=campagne_id,
        message_id=message_id,
        message_date_reception=message_date_reception,
        montant_initial=montant,
        montant_restant=montant,
        mode=Client.objects.values_list("mode_reglement", flat=True).get(id=client_id),
    )


def _reservation_verrouillee(reservation_id) -> tuple[CompteCredit, Reservation]:
    client_id = Reservation.objects.values_list("client_id", flat=True).get(id=reservation_id)
    compte = _compte_verrouille(client_id)
    reservation = Reservation.objects.select_for_update().get(id=reservation_id)
    if reservation.etat != Reservation.Etat.ACTIVE:
        raise ReservationClose(f"Réservation {reservation.id} déjà {reservation.etat}.")
    return compte, reservation


@transaction.atomic
def regler(reservation_id, montant: int, *, reference: str = "") -> Ecriture:
    """Consomme `montant` sur une réservation active : écriture CONSOMMATION au
    registre. La réservation passe REGLEE quand elle est entièrement consommée."""
    _montant_positif(montant)
    compte, reservation = _reservation_verrouillee(reservation_id)
    if montant > reservation.montant_restant:
        raise ValueError(
            f"Règlement de {montant} FCFA supérieur au reste réservé "
            f"({reservation.montant_restant})."
        )
    reservation.montant_restant -= montant
    compte.montant_reserve -= montant
    if reservation.montant_restant == 0:
        reservation.etat = Reservation.Etat.REGLEE
        reservation.resolue_le = timezone.now()
    reservation.save(update_fields=["montant_restant", "etat", "resolue_le"])
    return _ecrire(compte, Ecriture.Type.CONSOMMATION, -montant, reference=reference)


@transaction.atomic
def liberer(reservation_id) -> int:
    """Rend au solde disponible ce qui reste d'une réservation, sans aucune trace
    comptable. Renvoie le montant libéré."""
    compte, reservation = _reservation_verrouillee(reservation_id)
    libere = reservation.montant_restant
    compte.montant_reserve -= libere
    compte.save(update_fields=["montant_reserve", "mis_a_jour_le"])
    reservation.montant_restant = 0
    reservation.etat = Reservation.Etat.LIBEREE
    reservation.resolue_le = timezone.now()
    reservation.save(update_fields=["montant_restant", "etat", "resolue_le"])
    return libere


def reference_message(message_id) -> str:
    return f"message:{message_id}"


# États finals qui consomment en règlement à la livraison : livré, et sans
# accusé, qui n'est pas un échec avéré (décision du 6 novembre 2026).
CONSOMMENT_A_LA_LIVRAISON = frozenset({Etat.LIVRE, Etat.SANS_ACCUSE})


def _reservation_de(compte: CompteCredit, message_id, campagne_id) -> Reservation:
    filtre = {"campagne_id": campagne_id} if campagne_id else {"message_id": message_id}
    reservation = Reservation.objects.filter(client_id=compte.client_id, **filtre).first()
    if reservation is None:
        raise SansReservation(f"Aucune réservation pour {reference_message(message_id)}.")
    return reservation


def _consommer(reservation: Reservation, montant: int, reference: str, campagne_id):
    if campagne_id and reservation.etat == Reservation.Etat.REGLEE:
        return None  # coût annoncé entièrement consommé : plus rien à facturer
    if reservation.etat != Reservation.Etat.ACTIVE:
        raise ReservationClose(f"Réservation {reservation.id} déjà close pour {reference}.")
    if campagne_id:
        # Le coût réel d'une campagne ne dépasse jamais le coût annoncé : un
        # message plus long que prévu (contact modifié depuis la confirmation)
        # consomme au plus ce qui reste de la réservation.
        montant = min(montant, reservation.montant_restant)
        if montant == 0:
            return None
    return regler(reservation.id, montant, reference=reference)


def _rendre(reservation: Reservation, campagne_id) -> None:
    """Un message qui ne coûte rien : la réservation propre au message est
    libérée ; celle d'une campagne attend la fin de la campagne."""
    if not campagne_id and reservation.etat == Reservation.Etat.ACTIVE:
        liberer(reservation.id)


@transaction.atomic
def solder_message(
    *, client_id, message_id, campagne_id=None, montant: int, soumis: bool
) -> Ecriture | None:
    """Suite comptable d'une soumission, `montant` étant le coût figé sur le
    message.

    Règlement à la soumission : soumis, le message consomme sur la réservation
    de sa campagne, ou sur la sienne hors campagne ; refusé avant le réseau, il
    ne coûte rien. Règlement à la livraison : soumis, il attend son état final
    (`solder_final`) ; refusé avant le réseau, il ne coûte rien non plus.
    Idempotent : une relivraison de la soumission ne consomme pas deux fois, ce
    que la base garantit aussi.
    """
    compte = _compte_verrouille(client_id)
    reference = reference_message(message_id)
    if Ecriture.objects.filter(type=Ecriture.Type.CONSOMMATION, reference=reference).exists():
        return None
    reservation = _reservation_de(compte, message_id, campagne_id)
    if not soumis:
        _rendre(reservation, campagne_id)
        return None
    if reservation.mode == Client.Reglement.LIVRAISON:
        return None  # réglé à son état final
    return _consommer(reservation, montant, reference, campagne_id)


@transaction.atomic
def solder_final(
    *, client_id, message_id, campagne_id=None, montant: int, etat: str
) -> Ecriture | None:
    """Suite comptable de l'état final d'un message soumis. Ne fait rien en
    règlement à la soumission, où tout s'est joué à la soumission. En règlement
    à la livraison : livré ou sans accusé, le message consomme son coût figé ;
    non livré, expiré ou rejeté par l'opérateur, il ne coûte rien. Idempotent.
    """
    compte = _compte_verrouille(client_id)
    reservation = _reservation_de(compte, message_id, campagne_id)
    # Le mode d'abord : en règlement à la soumission, la réservation est
    # souvent déjà close, et ce n'est pas une anomalie.
    if reservation.mode != Client.Reglement.LIVRAISON:
        return None
    reference = reference_message(message_id)
    if Ecriture.objects.filter(type=Ecriture.Type.CONSOMMATION, reference=reference).exists():
        return None
    if etat in CONSOMMENT_A_LA_LIVRAISON:
        return _consommer(reservation, montant, reference, campagne_id)
    _rendre(reservation, campagne_id)
    return None


def solde(client_id) -> Solde:
    """Solde tel que le cache le tient, pour l'affichage."""
    compte = CompteCredit.objects.filter(client_id=client_id).first()
    if compte is None:
        return Solde(0, 0)
    return Solde(compte.solde_acquis, compte.montant_reserve)


def solde_recalcule(client_id) -> Solde:
    """Solde reconstitué depuis le registre et les réservations actives. Sert au
    rapprochement avec le cache, jamais à l'affichage."""
    acquis = Ecriture.objects.filter(client_id=client_id).aggregate(total=Sum("montant"))["total"]
    reserve = Reservation.objects.filter(
        client_id=client_id, etat=Reservation.Etat.ACTIVE
    ).aggregate(total=Sum("montant_restant"))["total"]
    return Solde(acquis or 0, reserve or 0)
