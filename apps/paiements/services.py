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
import math
from dataclasses import dataclass
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.core.audit import auditer
from apps.core.courriels import envoyer
from apps.core.evenements import publier
from apps.credits import services as credits
from apps.fichiers.models import Fichier
from apps.fichiers.services import FichierRefuse, deposer

from . import fournisseurs
from .models import Reglage, Transaction

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
    if transaction_.initie_par_id:
        # Même si le client a fermé son navigateur : le courriel part au commit.
        envoyer(
            transaction_.initie_par.courriel,
            "Rechargement confirmé",
            f"Votre paiement de {transaction_.montant} FCFA est confirmé : "
            f"{transaction_.credits} FCFA de crédits sont portés à votre solde.\n\n"
            f"Référence : {transaction_.reference_transaction}\n"
            f"{settings.PORTAIL_URL}/paiements/{transaction_.id}\n",
        )
    publier(
        "paiements.transaction.confirmee",
        client_id=transaction_.client_id,
        objet_id=transaction_.id,
        donnees={"credits": transaction_.credits, "montant": transaction_.montant},
        cle_idempotence=f"paiement-confirme:{transaction_.id}",
    )


# Rechargement en ligne (T-86)


class RechargementRefuse(ValueError):
    pass


@dataclass(frozen=True)
class Montants:
    credits: int
    tva: int
    ttc: int


def reglage() -> Reglage:
    """La ligne des réglages, ou ses valeurs par défaut tant qu'elle n'existe pas."""
    return Reglage.objects.filter(id=1).first() or Reglage()


CHAMPS_REGLAGE = ("tva_active", "taux_tva", "credits_min", "credits_max", "delai_minutes")


@transaction.atomic
def regler(reglage_: Reglage, *, agent, adresse_ip=None) -> Reglage:
    """Le back-office change la TVA, les bornes ou le délai du rechargement.
    Une seule ligne, tracée au journal d'audit."""
    ancien = Reglage.objects.filter(id=1).first()
    avant = {c: getattr(ancien, c) for c in CHAMPS_REGLAGE} if ancien else None
    reglage_.id = 1
    reglage_.full_clean()
    reglage_.save()
    auditer(
        "paiements.reglage.modifie",
        reglage_,
        auteur=agent,
        avant=avant,
        apres={c: getattr(reglage_, c) for c in CHAMPS_REGLAGE},
        adresse_ip=adresse_ip,
    )
    return reglage_


def pas_d_achat(reglage_: Reglage) -> int:
    """Le plus petit multiple de crédits HT dont la TVA tombe sur un franc
    entier : 50 à 18 %, 1 sans TVA."""
    if not reglage_.tva_active or not reglage_.taux_tva:
        return 1
    return 100 // math.gcd(100, reglage_.taux_tva)


def montants(credits_ht: int, reglage_: Reglage | None = None, *, bornes=True) -> Montants:
    """Ce que coûtent des crédits HT, TVA comprise. Refuse un montant hors du pas
    ou des bornes : jamais d'arrondi sur une somme d'argent."""
    reglage_ = reglage_ or reglage()
    if not isinstance(credits_ht, int) or isinstance(credits_ht, bool) or credits_ht <= 0:
        raise RechargementRefuse("Le montant de crédits est un nombre entier de FCFA.")
    pas = pas_d_achat(reglage_)
    if credits_ht % pas:
        raise RechargementRefuse(f"Les crédits s'achètent par multiples de {pas} FCFA.")
    if bornes and reglage_.credits_min is not None and credits_ht < reglage_.credits_min:
        raise RechargementRefuse(f"Rechargement minimal : {reglage_.credits_min} FCFA.")
    if bornes and reglage_.credits_max is not None and credits_ht > reglage_.credits_max:
        raise RechargementRefuse(f"Rechargement maximal : {reglage_.credits_max} FCFA.")
    tva = credits_ht * reglage_.taux_tva // 100 if reglage_.tva_active else 0
    return Montants(credits_ht, tva, credits_ht + tva)


def initier(client, *, credits_ht: int, fournisseur_nom: str, auteur) -> tuple[Transaction, str]:
    """Lance un rechargement : la transaction est écrite INITIE, le fournisseur
    ouvre le paiement hors de toute transaction, puis elle passe EN_ATTENTE avec
    sa référence. Rend l'adresse où envoyer le client payer."""
    fournisseur = fournisseurs.actifs().get(fournisseur_nom)
    if fournisseur is None:
        raise RechargementRefuse("Moyen de paiement indisponible.")
    reglage_ = reglage()
    calcul = montants(credits_ht, reglage_)
    transaction_ = Transaction.objects.create(
        client=client,
        fournisseur=fournisseur.code,
        montant=calcul.ttc,
        credits=calcul.credits,
        initie_par=auteur,
        expire_le=timezone.now() + timedelta(minutes=reglage_.delai_minutes),
    )
    ouverture = fournisseur.initier(montant=calcul.ttc, transaction_id=transaction_.id)
    Transaction.objects.filter(id=transaction_.id, etat=Etat.INITIE).update(
        reference_transaction=ouverture.reference, etat=Etat.EN_ATTENTE
    )
    transaction_.refresh_from_db()
    return transaction_, ouverture.adresse


A_RELANCER = 200
REPRISE_DES_EXPIRES = timedelta(days=1)


def relancer(maintenant=None, *, reprendre_expires=False) -> int:
    """Balayage de l'ordonnanceur : chaque paiement en attente est revérifié
    auprès de son fournisseur, rappel reçu ou non, pour qu'un paiement confirmé
    pendant que le client a fermé son navigateur, ou dont le rappel s'est
    perdu, soit crédité quand même. Au-delà du délai, ce qui attend toujours
    passe EXPIRE ; un rappel tardif confirmé le reprendra. Avec
    `reprendre_expires`, à cadence plus lente, les paiements expirés depuis moins
    d'un jour sont aussi revérifiés : payé après le délai et sans rappel, un
    paiement serait sinon perdu pour le client. Sous le rôle worker."""
    maintenant = maintenant or timezone.now()
    a_verifier = Q(etat=Etat.EN_ATTENTE)
    if reprendre_expires:
        a_verifier |= Q(etat=Etat.EXPIRE, expire_le__gte=maintenant - REPRISE_DES_EXPIRES)
    en_attente = list(
        Transaction.objects.filter(a_verifier)
        .exclude(reference_transaction="")
        .order_by("initie_le")
        .values_list("fournisseur", "reference_transaction")[:A_RELANCER]
    )
    for fournisseur_code, reference in en_attente:
        try:
            verifier(fournisseur_code, reference)
        except Exception:
            journal.exception("Relance du paiement %s %s impossible.", fournisseur_code, reference)
    # Une mise à jour par ligne : celle qu'un vérificateur tient verrouillée attend,
    # puis n'est plus EN_ATTENTE si elle vient d'être confirmée.
    return Transaction.objects.filter(
        Q(etat=Etat.EN_ATTENTE) | Q(etat=Etat.INITIE), expire_le__lt=maintenant
    ).update(etat=Etat.EXPIRE)


# Paiements manuels (T-87)

MANUELS = (Transaction.Fournisseur.VIREMENT, Transaction.Fournisseur.ESPECES)


class SaisieRefusee(ValueError):
    pass


@transaction.atomic
def saisir_manuel(
    client,
    *,
    fournisseur: str,
    credits_ht: int,
    reference: str,
    motif: str,
    justificatif: bytes | None,
    nom_justificatif: str = "",
    agent,
    adresse_ip=None,
) -> Transaction:
    """Un agent saisit un virement ou un versement en espèces reçu. Le
    justificatif est déposé, la transaction écrite CONFIRME, et l'ACHAT porte
    l'agent et le motif : un paiement manuel est un crédit payé, jamais un
    ajustement. Les bornes du rechargement en ligne ne s'appliquent pas, le pas
    d'achat si. Sous le rôle du back-office."""
    reference, motif = (reference or "").strip(), (motif or "").strip()
    if fournisseur not in MANUELS:
        raise SaisieRefusee("Un paiement manuel est un virement ou un versement en espèces.")
    if agent is None:
        raise SaisieRefusee("Un paiement manuel porte l'agent qui le saisit.")
    if not justificatif:
        raise SaisieRefusee("Aucune saisie manuelle sans justificatif téléversé.")
    if not motif:
        raise SaisieRefusee("Un paiement manuel exige un motif.")
    if not reference:
        raise SaisieRefusee("Indiquez la référence du virement ou le numéro du reçu.")
    try:
        calcul = montants(credits_ht, reglage(), bornes=False)
    except RechargementRefuse as refus:
        raise SaisieRefusee(str(refus)) from None
    if Transaction.objects.filter(
        fournisseur=fournisseur, reference_transaction=reference
    ).exists():
        raise SaisieRefusee("Ce paiement est déjà saisi sous cette référence.")
    try:
        fichier = deposer(
            client=client,
            famille=Fichier.Famille.PAIEMENT,
            contenu=justificatif,
            nom_origine=nom_justificatif or "justificatif",
        )
    except FichierRefuse as refus:
        raise SaisieRefusee(str(refus)) from None
    transaction_ = Transaction.objects.create(
        client=client,
        fournisseur=fournisseur,
        reference_transaction=reference,
        montant=calcul.ttc,
        credits=calcul.credits,
        etat=Etat.CONFIRME,
        confirme_le=timezone.now(),
        justificatif=fichier,
        saisi_par=agent,
        motif=motif,
    )
    credits.acheter(
        client.id,
        calcul.credits,
        reference=reference_registre(transaction_.id),
        auteur=agent,
        motif=motif,
    )
    auditer(
        "paiements.manuel.saisi",
        transaction_,
        auteur=agent,
        client=client,
        apres={
            "fournisseur": fournisseur,
            "reference": reference,
            "credits": calcul.credits,
            "montant": calcul.ttc,
            "justificatif": str(fichier.id),
        },
        motif=motif,
        adresse_ip=adresse_ip,
    )
    publier(
        "paiements.transaction.confirmee",
        client_id=client.id,
        objet_id=transaction_.id,
        donnees={"credits": calcul.credits, "montant": calcul.ttc, "manuel": True},
        cle_idempotence=f"paiement-confirme:{transaction_.id}",
    )
    return transaction_
