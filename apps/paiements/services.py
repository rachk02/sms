"""Porte d'écriture du module paiements.

Vérification et crédit (T-85). Le rappel d'un fournisseur n'est jamais la
preuve d'un paiement : c'est le signal d'aller interroger son API de statut.
Seul un statut CONFIRME, sur une transaction connue de la plateforme et au
montant attendu, fait passer la transaction à CONFIRME et écrit l'ACHAT au
registre de crédits, une fois et une seule :

1. l'API de statut est interrogée hors de toute transaction, pour qu'aucun
   appel réseau ne tienne un verrou ;
2. la ligne de la transaction est verrouillée (SELECT ... FOR UPDATE) : deux
   rappels traités en même temps s'attendent ;
3. sous ce verrou, une transaction déjà CONFIRME ne change plus : le rappel
   réémis, rejoué ou concurrent ne crédite rien de plus. L'unicité de
   (fournisseur, référence) empêche qu'une même référence porte deux
   transactions.
"""

import logging

from django.db import transaction
from django.utils import timezone

from apps.core.evenements import publier
from apps.credits import services as credits

from . import fournisseurs
from .models import Transaction

journal = logging.getLogger(__name__)

Etat = Transaction.Etat

# Un paiement passé EXPIRE par délai, que l'API de statut confirme ensuite, est
# repris et crédité : le refuser produirait des réclamations légitimes.
TRANSITIONS = {
    Etat.INITIE: {Etat.EN_ATTENTE, Etat.CONFIRME, Etat.ECHOUE, Etat.EXPIRE},
    Etat.EN_ATTENTE: {Etat.CONFIRME, Etat.ECHOUE, Etat.EXPIRE},
    Etat.EXPIRE: {Etat.CONFIRME},
    Etat.ECHOUE: set(),
    Etat.CONFIRME: {Etat.REMBOURSE},
    Etat.REMBOURSE: set(),
}


def reference_registre(transaction_id) -> str:
    """Référence portée par l'écriture ACHAT d'une transaction."""
    return f"paiement:{transaction_id}"


def verifier(fournisseur_code: str, reference: str) -> Transaction | None:
    """Vérifie une référence annoncée par un rappel, puis crédite si le paiement
    est confirmé. Une erreur réseau remonte : le worker réessaie plus tard."""
    fournisseur = fournisseurs.par_code(fournisseur_code)
    if fournisseur is None:
        journal.warning("Vérification impossible : fournisseur %s inactif.", fournisseur_code)
        return None
    statut = fournisseur.statut(reference)
    return appliquer_statut(fournisseur_code, reference, statut)


def _alerter(transaction_: Transaction, motif: str, **donnees) -> None:
    journal.error(
        "Paiement %s (%s %s) : %s.",
        transaction_.id,
        transaction_.fournisseur,
        transaction_.reference_transaction,
        motif,
        extra={"transaction_id": str(transaction_.id)},
    )
    publier(
        "paiements.transaction.anomalie",
        client_id=transaction_.client_id,
        objet_id=transaction_.id,
        donnees={"motif": motif, **donnees},
    )


@transaction.atomic
def appliquer_statut(
    fournisseur_code: str, reference: str, statut: fournisseurs.Statut
) -> Transaction | None:
    """Applique ce que l'API de statut a répondu, sous le verrou de la ligne."""
    transaction_ = (
        Transaction.objects.select_for_update()
        .filter(fournisseur=fournisseur_code, reference_transaction=reference)
        .first()
    )
    if transaction_ is None:
        # Rappel forgé, ou référence d'un autre marchand : rien à créditer, même
        # si le fournisseur la dit payée.
        journal.warning(
            "Rappel %s pour une référence inconnue (%s), statut %s : rien n'est crédité.",
            fournisseur_code,
            reference[:100],
            statut.etat,
        )
        return None
    if statut.etat == Etat.CONFIRME:
        _confirmer(transaction_, statut)
    elif statut.etat == Etat.ECHOUE and Etat.ECHOUE in TRANSITIONS[transaction_.etat]:
        transaction_.etat = Etat.ECHOUE
        transaction_.save(update_fields=["etat"])
    return transaction_


def _confirmer(transaction_: Transaction, statut: fournisseurs.Statut) -> None:
    if transaction_.etat == Etat.CONFIRME:
        return  # déjà crédité : rappel réémis ou rejoué
    if Etat.CONFIRME not in TRANSITIONS[transaction_.etat]:
        _alerter(transaction_, f"confirmé par le fournisseur alors que {transaction_.etat}")
        return
    if statut.montant != transaction_.montant:
        _alerter(
            transaction_,
            "montant confirmé différent du montant attendu",
            attendu=transaction_.montant,
            confirme=statut.montant,
        )
        return
    transaction_.etat = Etat.CONFIRME
    transaction_.confirme_le = timezone.now()
    transaction_.save(update_fields=["etat", "confirme_le"])
    credits.acheter(
        transaction_.client_id,
        transaction_.credits,
        reference=reference_registre(transaction_.id),
        auteur=transaction_.saisi_par,
    )
    publier(
        "paiements.transaction.confirmee",
        client_id=transaction_.client_id,
        objet_id=transaction_.id,
        donnees={"credits": transaction_.credits, "montant": transaction_.montant},
        cle_idempotence=f"paiement-confirme:{transaction_.id}",
    )
