"""Normalisation des numéros de téléphone au format international E.164.

Le numéro normalisé est la seule forme stockée et comparée : `+` suivi de
l'indicatif du pays et du numéro national, sans aucun séparateur. La
classification par opérateur n'est pas faite ici, elle dépend de la table des
préfixes tenue en base (T-44).
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class PlanNumerotation:
    indicatif: str
    longueur_nationale: int


# Un seul pays servi au lancement. Un pays de plus se déclare ici, puis passe en
# base avec la fiche F-47.
PLANS = {
    "BF": PlanNumerotation(indicatif="226", longueur_nationale=8),
}

SEPARATEURS = re.compile(r"[\s.\-/() ]")


class NumeroInvalide(ValueError):
    def __init__(self, numero: str, cause: str):
        self.numero = numero
        self.cause = cause
        super().__init__(f"Numéro invalide ({cause}) : {numero!r}")


def normaliser(numero: str, pays: str = "BF") -> str:
    plan = PLANS[pays]
    brut = numero
    numero = SEPARATEURS.sub("", numero or "")
    if not numero:
        raise NumeroInvalide(brut, "numéro vide")

    international = False
    if numero.startswith("+"):
        numero, international = numero[1:], True
    elif numero.startswith("00"):
        numero, international = numero[2:], True

    if not (numero.isascii() and numero.isdigit()):
        raise NumeroInvalide(brut, "caractères non numériques")

    longueur_complete = len(plan.indicatif) + plan.longueur_nationale
    if international or (numero.startswith(plan.indicatif) and len(numero) == longueur_complete):
        if not numero.startswith(plan.indicatif):
            raise NumeroInvalide(brut, "indicatif d'un pays non servi")
        national = numero[len(plan.indicatif) :]
    else:
        national = numero

    if len(national) < plan.longueur_nationale:
        raise NumeroInvalide(brut, "numéro trop court")
    if len(national) > plan.longueur_nationale:
        raise NumeroInvalide(brut, "numéro trop long")
    return f"+{plan.indicatif}{national}"
