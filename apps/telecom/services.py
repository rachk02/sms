"""Porte d'écriture du module telecom."""

from datetime import datetime

from .models import MessageEntrant
from .mots_cles import reconnaitre


def enregistrer_entrant(
    *,
    connecteur_id,
    reference: str,
    numero_expediteur: str,
    numero_destination: str,
    texte: str,
    recu_le: datetime,
) -> tuple[MessageEntrant, bool]:
    """Écrit un SMS entrant et le mot-clé qu'il porte, s'il en porte un. Sans
    effet si la passerelle rejoue le même rappel : renvoie le message et vrai
    s'il vient d'être créé."""
    mot_cle = reconnaitre(texte)
    return MessageEntrant.objects.get_or_create(
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
