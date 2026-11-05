"""Porte d'écriture du module telecom."""

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.core.evenements import publier

from .models import MessageEntrant, MotCle
from .mots_cles import reconnaitre

# Un STOP reçu est annoncé ; le journal, au-dessus, le rapproche des clients qui
# ont écrit à ce numéro (T-70).
DESINSCRIPTION_RECUE = "telecom.desinscription.recue"


def enregistrer_entrant(
    *,
    connecteur_id,
    reference: str,
    numero_expediteur: str,
    numero_destination: str,
    texte: str,
    recu_le: datetime,
) -> tuple[MessageEntrant, bool]:
    """Écrit un SMS entrant et le mot-clé qu'il porte, s'il en porte un, et
    annonce une désinscription dans la même transaction. Sans effet si la
    passerelle rejoue le même rappel : renvoie le message et vrai s'il vient
    d'être créé."""
    mot_cle = reconnaitre(texte)
    with transaction.atomic():
        entrant, cree = MessageEntrant.objects.get_or_create(
            connecteur_id=connecteur_id,
            reference=reference,
            defaults={
                "numero_expediteur": numero_expediteur,
                "numero_destination": numero_destination,
                "texte": texte,
                "mot_cle_reconnu": mot_cle.mot_cle if mot_cle else "",
                "recu_le": recu_le,
            },
        )
        if cree and mot_cle and mot_cle.action == MotCle.Action.DESINSCRIPTION:
            publier(
                DESINSCRIPTION_RECUE,
                objet_id=entrant.id,
                donnees={
                    "numero_expediteur": entrant.numero_expediteur,
                    "texte": entrant.texte,
                    "recu_le": entrant.recu_le.isoformat(),
                },
                cle_idempotence=f"desinscription:{entrant.id}",
            )
    return entrant, cree


def marquer_rapproche(entrant_id, clients: list) -> None:
    """Note qu'un SMS entrant a été rapproché. Le client n'est posé que s'il est
    seul : un STOP qui vaut pour plusieurs clients reste sans client, rapproché
    quand même, et chaque client le retrouve dans sa liste noire."""
    MessageEntrant.objects.filter(id=entrant_id, traite_le__isnull=True).update(
        client_id=clients[0] if len(clients) == 1 else None, traite_le=timezone.now()
    )


def connecteur_pour(operateur: str):
    """Connecteur qui porte les messages d'un opérateur : le premier actif dont
    la liaison n'est pas tombée. Le routage par priorité et la bascule sont
    l'objet de la fiche F-38 (lot 10) ; ceci en tient lieu d'ici là."""
    from .models import Connecteur

    return (
        Connecteur.objects.filter(operateur=operateur, actif=True)
        .exclude(etat_liaison=Connecteur.EtatLiaison.DECONNECTE)
        .order_by("cree_le")
        .first()
    )
