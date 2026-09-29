import pytest

from apps.core.numeros import NumeroInvalide, normaliser

ATTENDU = "+22670123456"


@pytest.mark.parametrize(
    "saisie",
    [
        "70123456",
        "22670123456",
        "+22670123456",
        "0022670123456",
        "70 12 34 56",
        "70.12.34.56",
        "70-12-34-56",
        "+226 70 12 34 56",
        "00226.70.12.34.56",
        "226-70-12-34-56",
        " 70 12 34 56 ",
        "70 12 34 56",
    ],
)
def test_toutes_les_formes_courantes_donnent_la_forme_internationale(saisie):
    assert normaliser(saisie) == ATTENDU


@pytest.mark.parametrize(
    ("saisie", "cause"),
    [
        ("", "numéro vide"),
        ("   ", "numéro vide"),
        (None, "numéro vide"),
        ("abcdefgh", "caractères non numériques"),
        ("70I23456", "caractères non numériques"),
        ("７０123456", "caractères non numériques"),
        ("7012345²", "caractères non numériques"),
        ("7012345", "numéro trop court"),
        ("+2267012345", "numéro trop court"),
        ("701234567", "numéro trop long"),
        ("+226701234567", "numéro trop long"),
        ("+22370123456", "indicatif d'un pays non servi"),
        ("0033612345678", "indicatif d'un pays non servi"),
    ],
)
def test_un_numero_invalide_leve_une_erreur_qui_nomme_la_cause(saisie, cause):
    with pytest.raises(NumeroInvalide) as erreur:
        normaliser(saisie)
    assert erreur.value.cause == cause
    assert cause in str(erreur.value)
