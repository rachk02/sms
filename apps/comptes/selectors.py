"""Lectures du module comptes."""

from .models import Utilisateur


def courriels_a_prevenir(client_id, utilisateur_id) -> list[str]:
    """Où envoyer un courriel destiné à un membre du compte, relu au moment de
    l'envoi : à lui s'il est encore membre actif de ce compte, sinon aux
    administrateurs actifs du compte. Un membre retiré ne reçoit plus rien."""
    courriel = (
        Utilisateur.objects.filter(pk=utilisateur_id, client_id=client_id, is_active=True)
        .values_list("courriel", flat=True)
        .first()
    )
    if courriel:
        return [courriel]
    return list(
        Utilisateur.objects.filter(
            client_id=client_id, role=Utilisateur.Role.ADMIN, is_active=True
        ).values_list("courriel", flat=True)
    )


def membre_actif(client_id, courriel: str) -> bool:
    """Vrai si l'adresse est celle d'un membre actif de ce compte, à cet instant."""
    return Utilisateur.objects.filter(
        client_id=client_id, courriel=courriel, is_active=True
    ).exists()
