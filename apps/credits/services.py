"""Porte d'écriture du module credits.

Toute opération sur le solde prend d'abord le verrou de la ligne de cache du
client (`credits_compte`, SELECT ... FOR UPDATE), puis celui de la réservation
concernée, toujours dans cet ordre : deux opérations concurrentes sur un même
client s'attendent au lieu de se croiser, et deux clients ne se gênent pas.
"""

from dataclasses import dataclass

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from .models import CompteCredit, Ecriture, Reservation


class SoldeInsuffisant(Exception):
    def __init__(self, disponible: int, demande: int):
        self.disponible = disponible
        self.demande = demande
        super().__init__(f"Solde insuffisant : {disponible} FCFA disponibles, {demande} demandés.")


class MotifObligatoire(ValueError):
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


def _compte_verrouille(client_id) -> CompteCredit:
    CompteCredit.objects.bulk_create([CompteCredit(client_id=client_id)], ignore_conflicts=True)
    return CompteCredit.objects.select_for_update().get(client_id=client_id)


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
    """Achat constaté : paiement confirmé de serveur à serveur, ou virement validé."""
    _montant_positif(montant)
    compte = _compte_verrouille(client_id)
    return _ecrire(compte, Ecriture.Type.ACHAT, montant, reference=reference, auteur=auteur)


@transaction.atomic
def recrediter(client_id, montant: int, *, motif: str = "", reference: str = "", auteur=None):
    """Recrédit : défaillance imputable à la plateforme, ou geste commercial."""
    _montant_positif(montant)
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
    envoi unitaire. Lève SoldeInsuffisant si le solde ne couvre pas tout."""
    _montant_positif(montant)
    compte = _compte_verrouille(client_id)
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


@transaction.atomic
def solder_message(
    *, client_id, message_id, campagne_id=None, montant: int, soumis: bool
) -> Ecriture | None:
    """Suite comptable d'une soumission. Jusqu'au lot 7, un message consomme son
    coût quand le réseau l'accepte (décision du 30 septembre 2026).

    Soumis : écriture CONSOMMATION sur la réservation de sa campagne, ou sur la
    sienne pour un envoi hors campagne. Refusé avant le réseau : rien n'est
    consommé ; la réservation propre au message est libérée, celle d'une
    campagne attend la fin de la campagne. Idempotent : une relivraison de la
    soumission ne consomme pas deux fois, ce que la base garantit aussi.
    """
    compte = _compte_verrouille(client_id)
    reference = reference_message(message_id)
    if Ecriture.objects.filter(type=Ecriture.Type.CONSOMMATION, reference=reference).exists():
        return None
    filtre = {"campagne_id": campagne_id} if campagne_id else {"message_id": message_id}
    reservation = Reservation.objects.filter(client_id=compte.client_id, **filtre).first()
    if reservation is None:
        raise SansReservation(f"Aucune réservation pour {reference}.")
    if reservation.etat != Reservation.Etat.ACTIVE:
        raise ReservationClose(f"Réservation {reservation.id} déjà close pour {reference}.")
    if soumis:
        return regler(reservation.id, montant, reference=reference)
    if not campagne_id:
        liberer(reservation.id)
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
