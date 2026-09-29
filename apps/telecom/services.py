"""Porte d'écriture du module telecom."""

from datetime import datetime

from .models import MessageEntrant


def enregistrer_entrant(
    *,
    connecteur_id,
    reference: str,
    numero_expediteur: str,
    numero_destination: str,
    texte: str,
    recu_le: datetime,
) -> tuple[MessageEntrant, bool]:
    """Écrit un SMS entrant. Sans effet si la passerelle rejoue le même rappel :
    renvoie le message et vrai s'il vient d'être créé."""
    return MessageEntrant.objects.get_or_create(
        connecteur_id=connecteur_id,
        reference=reference,
        defaults={
            "numero_expediteur": numero_expediteur,
            "numero_destination": numero_destination,
            "texte": texte,
            "recu_le": recu_le,
        },
    )
