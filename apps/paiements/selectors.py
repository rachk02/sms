"""Lectures du module paiements. Aucune écriture."""

from dataclasses import dataclass, field

from django.urls import reverse
from django.utils import timezone

from .models import Facture, Transaction

Etat = Transaction.Etat


@dataclass(frozen=True)
class Suivi:
    """Ce que la page d'attente dit d'un paiement. `termine` arrête la sonde : la
    page ne tourne jamais au-delà du délai du paiement."""

    titre: str
    message: str
    termine: bool
    reussi: bool = False
    suites: list = field(default_factory=list)


def suivi(transaction_: Transaction, maintenant=None) -> Suivi:
    maintenant = maintenant or timezone.now()
    recharger = ("Faire un autre rechargement", reverse("recharger"))
    if transaction_.etat == Etat.CONFIRME:
        return Suivi(
            "Rechargement confirmé",
            f"{transaction_.credits} FCFA de crédits sont portés à votre solde.",
            termine=True,
            reussi=True,
            suites=[("Composer une campagne", reverse("campagne-nouvelle")), recharger],
        )
    if transaction_.etat == Etat.ECHOUE:
        return Suivi(
            "Paiement refusé",
            "L'opérateur a refusé le paiement : aucun crédit n'a été ajouté.",
            termine=True,
            suites=[("Réessayer", reverse("recharger"))],
        )
    if transaction_.etat == Etat.EXPIRE or (
        transaction_.expire_le and maintenant >= transaction_.expire_le
    ):
        return Suivi(
            "Toujours sans confirmation de l'opérateur",
            "Le délai d'attente est passé sans confirmation. Si vous avez payé, vos "
            "crédits seront portés à votre solde dès que l'opérateur le confirmera, et "
            "vous recevrez un courriel. Sinon, aucun montant n'a été débité.",
            termine=True,
            suites=[recharger],
        )
    return Suivi(
        "En attente de la confirmation de l'opérateur",
        "Validez le paiement sur votre téléphone. Cette page se met à jour seule ; "
        "vous pouvez aussi la fermer : vos crédits seront portés dès la confirmation, "
        "et un courriel vous le dira.",
        termine=False,
    )


def factures(client, limite: int = 200):
    """Les factures et avoirs du compte, les plus récents d'abord."""
    return list(Facture.objects.filter(client=client).order_by("-emise_le", "-id")[:limite])
