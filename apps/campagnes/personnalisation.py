"""Balises de personnalisation et rendu d'un message par destinataire (T-55).

Une balise s'écrit entre accolades : `{prenom}`, ou avec sa valeur de repli
`{prenom|cher client}`. Le nom, le prénom et la ville exigent une valeur de
repli écrite dans la balise ; un champ personnalisé a la sienne, déclarée avec
lui, qu'une valeur écrite dans la balise remplace. Le numéro est toujours
connu. Un champ vide chez un contact donne donc toujours la valeur de repli,
jamais une chaîne vide au milieu d'une phrase : aucun « Bonjour , » ne part.

Les accolades sont réservées aux balises : une accolade seule est refusée à
l'enregistrement plutôt que partie telle quelle.
"""

import re
from dataclasses import dataclass

BALISE = re.compile(r"\{\s*([a-z0-9_]+)\s*(?:\|([^{}]*))?\}")
FIXES = ("nom", "prenom", "ville")
TOUJOURS_CONNUS = ("numero",)


class ModeleInvalide(ValueError):
    pass


@dataclass(frozen=True)
class Balise:
    nom: str
    repli: str


@dataclass(frozen=True)
class Gabarit:
    """Un texte découpé une fois pour toutes en morceaux fixes et balises, rendu
    ensuite pour chaque destinataire sans nouvelle analyse."""

    morceaux: tuple[str | Balise, ...]

    @property
    def balises(self) -> list[Balise]:
        return [m for m in self.morceaux if isinstance(m, Balise)]

    def rendre(self, contact) -> str:
        return "".join(
            m if isinstance(m, str) else (_valeur(contact, m.nom) or m.repli) for m in self.morceaux
        )


def _valeur(contact, nom: str) -> str:
    if nom == "numero":
        return contact.numero_normalise
    if nom in FIXES:
        return (getattr(contact, nom) or "").strip()
    return str((contact.champs or {}).get(nom, "")).strip()


def compiler(texte: str, replis_declares: dict[str, str]) -> Gabarit:
    """Analyse le texte d'un modèle. `replis_declares` : les champs personnalisés
    du client et leur valeur de repli. Lève ModeleInvalide, en clair."""
    if not texte.strip():
        raise ModeleInvalide("Le message est vide.")
    morceaux: list[str | Balise] = []
    position = 0
    for trouve in BALISE.finditer(texte):
        morceaux.append(texte[position : trouve.start()])
        nom, repli = trouve.group(1), (trouve.group(2) or "").strip()
        if nom in FIXES:
            if not repli:
                raise ModeleInvalide(
                    f"La balise {{{nom}}} demande une valeur de repli, par exemple "
                    f"{{{nom}|cher client}} : elle remplace un {nom} absent."
                )
        elif nom in replis_declares:
            repli = repli or replis_declares[nom]
        elif nom not in TOUJOURS_CONNUS:
            raise ModeleInvalide(f"Balise inconnue : {{{nom}}}. Déclarez d'abord ce champ.")
        morceaux.append(Balise(nom, repli))
        position = trouve.end()
    morceaux.append(texte[position:])
    for morceau in morceaux:
        if isinstance(morceau, str) and ("{" in morceau or "}" in morceau):
            raise ModeleInvalide(
                "Accolade mal fermée : les accolades sont réservées aux balises, "
                "comme {prenom|cher client}."
            )
    return Gabarit(tuple(m for m in morceaux if m != ""))
