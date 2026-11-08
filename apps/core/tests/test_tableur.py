"""Cellules d'export sans formule exécutable."""

import pytest

from apps.core.tableur import cellule, ligne


@pytest.mark.parametrize(
    "piege",
    [
        "=1+1",
        '=HYPERLINK("http://exemple.invalid";"x")',
        "+cmd|' /C calc'!A0",
        "-2+3",
        "@SUM(A1:A2)",
        "\t=1+1",
        "\r=1+1",
    ],
)
def test_une_cellule_qui_commence_comme_une_formule_est_neutralisee(piege):
    assert cellule(piege) == "'" + piege


@pytest.mark.parametrize(
    "valeur", ["+22670123456", "-50", "12,5", "Promotion", "", 25, None, "a=b"]
)
def test_les_autres_valeurs_sortent_telles_quelles(valeur):
    assert cellule(valeur) == valeur


def test_une_ligne_neutralise_chaque_cellule():
    assert ligne(["+22670123456", "=1+1", 3]) == "+22670123456;'=1+1;3\r\n"
