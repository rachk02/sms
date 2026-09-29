"""Choix de l'encodage d'un SMS et décompte de ses segments.

Un texte composé uniquement de caractères de l'alphabet GSM 03.38 part en GSM 7
bits : 160 unités pour un message simple, 153 par segment pour un message long,
l'en-tête de concaténation prenant le reste. Les caractères de la table
d'extension coûtent deux unités, un échappement et le caractère lui-même, et un
segment ne sépare jamais les deux.

Un seul caractère hors de ces tables fait basculer tout le message en UCS-2 :
70 unités pour un message simple, 67 par segment. L'unité est alors le mot de
16 bits, si bien qu'un emoji hors du plan multilingue de base en compte deux,
que la coupe ne sépare pas non plus.

Voir la charte, section « Encodage des SMS ». Les accents é è à ù ì ò ä ö ü ñ
font partie de l'alphabet GSM et ne coûtent rien de plus.
"""

from dataclasses import dataclass, field
from enum import StrEnum

# Alphabet GSM 03.38 par défaut, sans le caractère d'échappement (0x1B).
ALPHABET_GSM = frozenset(
    "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?"
    "¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà"
)

# Table d'extension : chaque caractère coûte deux unités.
EXTENSION_GSM = frozenset("\f^{}\\[~]|€")

GSM_SIMPLE, GSM_SEGMENT = 160, 153
UCS2_SIMPLE, UCS2_SEGMENT = 70, 67


class Encodage(StrEnum):
    GSM7 = "GSM7"
    UCS2 = "UCS2"


@dataclass(frozen=True)
class Analyse:
    encodage: Encodage
    unites: int
    segments: int
    hors_gsm: list[str] = field(default_factory=list)


def _cout_gsm(caractere: str) -> int:
    return 2 if caractere in EXTENSION_GSM else 1


def _cout_ucs2(caractere: str) -> int:
    return 2 if ord(caractere) > 0xFFFF else 1


def _segments(couts: list[int], simple: int, par_segment: int) -> int:
    total = sum(couts)
    if total <= simple:
        return 1
    segments, rempli = 1, 0
    for cout in couts:
        if rempli + cout > par_segment:
            segments, rempli = segments + 1, 0
        rempli += cout
    return segments


def analyser(texte: str) -> Analyse:
    """Encodage retenu, unités consommées et nombre de segments.

    Un texte vide compte pour un segment : le refus d'un texte vide relève du
    service d'envoi, pas du décompte.
    """
    hors_gsm = sorted({c for c in texte if c not in ALPHABET_GSM and c not in EXTENSION_GSM})
    if not hors_gsm:
        couts = [_cout_gsm(c) for c in texte]
        return Analyse(Encodage.GSM7, sum(couts), _segments(couts, GSM_SIMPLE, GSM_SEGMENT))
    couts = [_cout_ucs2(c) for c in texte]
    return Analyse(Encodage.UCS2, sum(couts), _segments(couts, UCS2_SIMPLE, UCS2_SEGMENT), hors_gsm)
