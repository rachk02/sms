"""Contrôles appliqués à chaque message juste avant sa soumission.

Le journal soumet ; les règles qui peuvent interdire un envoi appartiennent à
des modules qui sont au-dessus de lui (liste noire des contacts) ou à côté
(bac à sable des comptes). Plutôt que d'importer vers le haut, chaque module
enregistre ici ses contrôles au démarrage de l'application, et la soumission
les passe tous : aucun chemin d'envoi ne peut en oublier un.

Un contrôle reçoit le message (lu sous verrou) et rend un motif de refus, ou
None s'il n'a rien contre.
"""

from collections.abc import Callable

_CONTROLES: list[tuple[str, Callable]] = []


def enregistrer(nom: str, controle: Callable) -> None:
    if any(existant == nom for existant, _ in _CONTROLES):
        return
    _CONTROLES.append((nom, controle))


def refus(message) -> str | None:
    for _, controle in _CONTROLES:
        motif = controle(message)
        if motif:
            return motif
    return None


def enregistres() -> list[str]:
    return [nom for nom, _ in _CONTROLES]


# Retenues : une décision du client qui suspend ses messages sans les refuser
# (campagne en pause ou annulée). Une retenue rend RETENIR (le message attend
# sans être soumis, il repartira) ou ANNULER (il ne partira jamais), ou None.
RETENIR = "RETENIR"
ANNULER = "ANNULER"

_RETENUES: list[tuple[str, Callable]] = []


def enregistrer_retenue(nom: str, retenue: Callable) -> None:
    if any(existant == nom for existant, _ in _RETENUES):
        return
    _RETENUES.append((nom, retenue))


def retenue(message) -> str | None:
    for _, verdict in _RETENUES:
        decision = verdict(message)
        if decision:
            return decision
    return None
