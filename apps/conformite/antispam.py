"""Dictionnaire anti-spam (T-99) : examen du texte d'une campagne.

Le texte et chaque expression proscrite passent par la même normalisation,
puis la comparaison cherche l'expression dans le texte. La normalisation
défait les contournements évidents :

- casse et accents : « CRÉDIT » et « credit » se valent ;
- substitutions de caractères : 0 pour o, 1, l, ! ou | pour i, 3 ou € pour e,
  4 ou @ pour a, 5 ou $ pour s, 7 pour t, 8 pour b ;
- séparateurs : espaces, points, tirets et chiffres restants disparaissent,
  « c r e d i t » et « c.r.e.d.i.t » se lisent « credit » ;
- répétitions : « creeedit » se lit « credit ».

Les séparateurs disparaissant, une expression peut se trouver à cheval sur
deux mots ; une expression trop courte se retrouverait partout, d'où un minimum
de trois lettres normalisées.
"""

import unicodedata
from dataclasses import dataclass

LETTRES_MIN = 3

SUBSTITUTIONS = str.maketrans(
    {
        "0": "o",
        "1": "i",
        "l": "i",
        "!": "i",
        "|": "i",
        "3": "e",
        "€": "e",
        "4": "a",
        "@": "a",
        "5": "s",
        "$": "s",
        "7": "t",
        "8": "b",
    }
)


class TexteProscrit(ValueError):
    def __init__(self, constats: list):
        self.constats = constats
        noms = ", ".join(f"« {c.expression} »" for c in constats)
        motifs = "; ".join(c.motif for c in constats if c.motif)
        super().__init__(
            f"Texte refusé : {noms} figure au dictionnaire anti-spam"
            + (f" ({motifs})." if motifs else ".")
        )


@dataclass(frozen=True)
class Constat:
    expression: str
    gravite: str
    motif: str


def normaliser(texte: str) -> str:
    decompose = unicodedata.normalize("NFKD", (texte or "").casefold())
    sans_accents = "".join(c for c in decompose if not unicodedata.combining(c))
    lettres = [c for c in sans_accents.translate(SUBSTITUTIONS) if c.isalpha()]
    compact = []
    for lettre in lettres:
        if not compact or compact[-1] != lettre:
            compact.append(lettre)
    return "".join(compact)


def examiner(texte: str) -> list[Constat]:
    """Les expressions actives trouvées dans le texte, lues en base à chaque
    examen : une expression ajoutée vaut pour la composition suivante."""
    from .models import MotProscrit

    forme = normaliser(texte)
    return [
        Constat(mot.expression, mot.gravite, mot.motif)
        for mot in MotProscrit.objects.filter(actif=True).order_by("expression")
        if mot.forme and mot.forme in forme
    ]


def exiger_admis(texte: str) -> list[Constat]:
    """Lève TexteProscrit si une expression bloquante figure dans le texte ;
    rend sinon les avertissements."""
    from .models import MotProscrit

    constats = examiner(texte)
    bloquants = [c for c in constats if c.gravite == MotProscrit.Gravite.BLOCAGE]
    if bloquants:
        raise TexteProscrit(bloquants)
    return constats
