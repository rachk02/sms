"""Permissions par ressource et par action, vérifiées côté serveur à chaque appel.

Masquer un bouton ne protège rien : chaque vue, page comme fragment, déclare la
permission qu'elle exige et la route refuse d'elle-même. Deux rôles au
lancement (décision du 29 septembre 2026) ; les rôles suivants s'ajouteront à
cette table sans toucher aux vues.
"""

from functools import wraps

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied

from .models import Utilisateur

ADMIN, UTILISATEUR = Utilisateur.Role.ADMIN, Utilisateur.Role.UTILISATEUR

PERMISSIONS: dict[str, frozenset[str]] = {
    "compte.consulter": frozenset({ADMIN, UTILISATEUR}),
    "campagnes.composer": frozenset({ADMIN, UTILISATEUR}),
    "campagnes.lancer": frozenset({ADMIN, UTILISATEUR}),
    "membres.gerer": frozenset({ADMIN}),
    "contacts.consulter": frozenset({ADMIN, UTILISATEUR}),
    "contacts.importer": frozenset({ADMIN, UTILISATEUR}),
    "contacts.exporter": frozenset({ADMIN, UTILISATEUR}),
    "contacts.gerer": frozenset({ADMIN, UTILISATEUR}),
    # Les champs personnalisés règlent le compte entier et ses segments.
    "contacts.parametrer": frozenset({ADMIN}),
    "expediteurs.consulter": frozenset({ADMIN, UTILISATEUR}),
    # Déclarer un identifiant engage le compte entier auprès des opérateurs.
    "expediteurs.declarer": frozenset({ADMIN}),
    # Recharger engage l'argent du compte ; suivre un paiement se fait à deux.
    "credits.recharger": frozenset({ADMIN}),
    "credits.consulter": frozenset({ADMIN, UTILISATEUR}),
}


def autorise(utilisateur: Utilisateur, permission: str) -> bool:
    if permission not in PERMISSIONS:
        raise KeyError(f"Permission inconnue : {permission}")
    return (
        utilisateur.is_authenticated
        and utilisateur.client_id is not None
        and utilisateur.role in PERMISSIONS[permission]
    )


def permission_requise(permission: str):
    if permission not in PERMISSIONS:
        raise KeyError(f"Permission inconnue : {permission}")

    def decorateur(vue):
        @login_required
        @wraps(vue)
        def verifiee(request, *args, **kwargs):
            if not autorise(request.user, permission):
                raise PermissionDenied
            return vue(request, *args, **kwargs)

        verifiee.permission = permission
        return verifiee

    return decorateur
