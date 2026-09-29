"""Lecture de la configuration par variables d'environnement.

`prod` ne lit que l'environnement. `dev` et `recette` chargent en plus le
fichier `.env` à la racine du dépôt, sans jamais écraser une variable déjà
posée.
"""

import os
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured

RACINE = Path(__file__).resolve().parent.parent


def charger_fichier_env(chemin: Path = RACINE / ".env") -> None:
    if not chemin.exists():
        return
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        ligne = ligne.strip()
        if not ligne or ligne.startswith("#") or "=" not in ligne:
            continue
        nom, valeur = ligne.split("=", 1)
        os.environ.setdefault(nom.strip(), valeur.strip())


def env(nom: str, defaut: str | None = None) -> str:
    valeur = os.environ.get(nom, defaut)
    if valeur is None:
        raise ImproperlyConfigured(f"Variable d'environnement manquante : {nom}")
    return valeur


def env_liste(nom: str, defaut: str | None = None) -> list[str]:
    return [element.strip() for element in env(nom, defaut).split(",") if element.strip()]
