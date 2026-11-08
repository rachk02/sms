"""Cellules d'un export CSV, sans formule exécutable par un tableur.

Un tableur exécute une cellule qui commence par =, +, -, @, une tabulation ou
un retour chariot : un texte venu d'un tiers (SMS entrant, contact importé,
raison sociale saisie par un client) deviendrait une formule chez celui qui
ouvre l'export (lien piégé, appel externe). Une telle cellule sort précédée
d'une apostrophe, que le tableur affiche comme du texte.

Un nombre signé (un numéro +22670123456, un montant -50) n'exécute rien et
reste tel quel : la colonne des numéros garde sa forme normalisée.
"""

import csv
import io
import re

DECLENCHEURS = ("=", "+", "-", "@", "\t", "\r")
NOMBRE_SIGNE = re.compile(r"[+-]?\d+(?:[.,]\d+)?")


def cellule(valeur):
    """La valeur telle quelle, sauf un texte qu'un tableur prendrait pour une
    formule, alors précédé d'une apostrophe."""
    if isinstance(valeur, str) and valeur.startswith(DECLENCHEURS):
        if not NOMBRE_SIGNE.fullmatch(valeur):
            return "'" + valeur
    return valeur


def cellules(valeurs) -> list:
    return [cellule(valeur) for valeur in valeurs]


def ligne(valeurs, *, separateur: str = ";") -> str:
    """Une ligne CSV, cellules neutralisées."""
    tampon = io.StringIO()
    csv.writer(tampon, delimiter=separateur).writerow(cellules(valeurs))
    return tampon.getvalue()
