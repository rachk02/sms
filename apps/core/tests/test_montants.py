"""Nombres et montants en toutes lettres (confirmation de campagne, T-61)."""

import pytest

from apps.core.montants import en_lettres, francs_cfa


@pytest.mark.parametrize(
    "nombre, lettres",
    [
        (0, "zéro"),
        (1, "un"),
        (16, "seize"),
        (17, "dix-sept"),
        (21, "vingt et un"),
        (22, "vingt-deux"),
        (61, "soixante et un"),
        (70, "soixante-dix"),
        (71, "soixante et onze"),
        (77, "soixante-dix-sept"),
        (80, "quatre-vingts"),
        (81, "quatre-vingt-un"),
        (90, "quatre-vingt-dix"),
        (91, "quatre-vingt-onze"),
        (99, "quatre-vingt-dix-neuf"),
        (100, "cent"),
        (101, "cent un"),
        (200, "deux cents"),
        (280, "deux cent quatre-vingts"),
        (1_000, "mille"),
        (1_001, "mille un"),
        (1_234, "mille deux cent trente-quatre"),
        (21_000, "vingt et un mille"),
        (80_000, "quatre-vingt mille"),
        (200_000, "deux cent mille"),
        (28_325, "vingt-huit mille trois cent vingt-cinq"),
        (1_000_000, "un million"),
        (2_000_000, "deux millions"),
        (80_000_000, "quatre-vingts millions"),
        (200_000_000, "deux cents millions"),
        (1_500_000_000, "un milliard cinq cents millions"),
        (40_000, "quarante mille"),
    ],
)
def test_un_nombre_s_ecrit_selon_l_orthographe_traditionnelle(nombre, lettres):
    assert en_lettres(nombre) == lettres


@pytest.mark.parametrize(
    "montant, lettres",
    [
        (1, "un franc CFA"),
        (2_500, "deux mille cinq cents francs CFA"),
        (2_000_000, "deux millions de francs CFA"),
        (2_000_020, "deux millions vingt francs CFA"),
    ],
)
def test_un_montant_s_ecrit_en_francs_cfa(montant, lettres):
    assert francs_cfa(montant) == lettres


def test_un_nombre_negatif_est_refuse():
    with pytest.raises(ValueError):
        en_lettres(-5)
