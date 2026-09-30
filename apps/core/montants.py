"""Nombres et montants en toutes lettres, en français.

La confirmation d'une campagne écrit le nombre de destinataires et le coût en
toutes lettres dans son texte même (T-61) : un zéro de trop se lit mieux en
mots qu'en chiffres. Orthographe traditionnelle : trait d'union entre dizaines
et unités sous cent seulement, « et » dans 21, 31... 71, pluriel de « vingt »
et « cent » quand rien ne les suit (« quatre-vingts », « deux cents », mais
« deux cent mille »), « mille » invariable, « million » et « milliard » noms.
"""

UNITES = (
    "zéro",
    "un",
    "deux",
    "trois",
    "quatre",
    "cinq",
    "six",
    "sept",
    "huit",
    "neuf",
    "dix",
    "onze",
    "douze",
    "treize",
    "quatorze",
    "quinze",
    "seize",
)
DIZAINES = {2: "vingt", 3: "trente", 4: "quarante", 5: "cinquante", 6: "soixante"}


def _moins_de_cent(n: int, final: bool) -> str:
    if n < 17:
        return UNITES[n]
    if n < 20:
        return f"dix-{UNITES[n - 10]}"
    dizaine, unite = divmod(n, 10)
    if dizaine == 7:
        return "soixante et onze" if unite == 1 else f"soixante-{_moins_de_cent(10 + unite, final)}"
    if dizaine == 8:
        if unite == 0:
            return "quatre-vingts" if final else "quatre-vingt"
        return f"quatre-vingt-{UNITES[unite]}"
    if dizaine == 9:
        return f"quatre-vingt-{_moins_de_cent(10 + unite, final)}"
    if unite == 0:
        return DIZAINES[dizaine]
    if unite == 1:
        return f"{DIZAINES[dizaine]} et un"
    return f"{DIZAINES[dizaine]}-{UNITES[unite]}"


def _moins_de_mille(n: int, final: bool) -> str:
    centaine, reste = divmod(n, 100)
    mots = []
    if centaine == 1:
        mots.append("cent")
    elif centaine > 1:
        mots.append(f"{UNITES[centaine]} cent" + ("s" if reste == 0 and final else ""))
    if reste:
        mots.append(_moins_de_cent(reste, final))
    return " ".join(mots)


def en_lettres(n: int) -> str:
    if not isinstance(n, int) or n < 0:
        raise ValueError(f"Entier positif ou nul attendu : {n!r}")
    if n == 0:
        return "zéro"
    milliards, reste = divmod(n, 10**9)
    millions, reste = divmod(reste, 10**6)
    milliers, unites = divmod(reste, 1000)
    mots = []
    if milliards:
        mots.append(f"{en_lettres(milliards)} milliard" + ("s" if milliards > 1 else ""))
    if millions:
        mots.append(f"{_moins_de_mille(millions, True)} million" + ("s" if millions > 1 else ""))
    if milliers:
        mots.append("mille" if milliers == 1 else f"{_moins_de_mille(milliers, False)} mille")
    if unites:
        mots.append(_moins_de_mille(unites, True))
    return " ".join(mots)


def francs_cfa(montant: int) -> str:
    """« deux mille cinq cents francs CFA », « deux millions de francs CFA »."""
    lettres = en_lettres(montant)
    if montant >= 10**6 and montant % 10**6 == 0:
        return f"{lettres} de francs CFA"
    return f"{lettres} franc{'s' if montant > 1 else ''} CFA"
