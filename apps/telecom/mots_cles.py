"""Reconnaissance des mots-clés dans les SMS entrants (T-69).

Le texte reçu est ramené à une forme canonique : majuscules, sans accent, tout
signe remplacé par une espace, espaces regroupées. Un message est reconnu
quand il ne contient qu'un mot-clé, entouré au plus de formules de politesse :
« stop », « Stop. », « arrêt », « stop svp ». Un mot-clé pris dans une phrase
(« je ne veux pas que ça s'arrête, stop ou encore ») n'est pas une demande :
le cas piégeux d'une désinscription qu'on n'a pas demandée.

La table des mots-clés est relue à chaque message entrant, dont le volume est
faible : ajouter un mot-clé en base change la reconnaissance sans redémarrage.
Les formules de politesse, elles, sont une donnée de la langue et non un réglage
du produit : elles restent dans le code.
"""

import re
import unicodedata

from .models import MotCle

# Formules tolérées avant ou après un mot-clé, sous leur forme canonique.
POLITESSE = (
    ("SVP",),
    ("STP",),
    ("MERCI",),
    ("S", "IL", "VOUS", "PLAIT"),
    ("SIL", "VOUS", "PLAIT"),
    ("S", "IL", "TE", "PLAIT"),
    ("SIL", "TE", "PLAIT"),
)

_SIGNE = re.compile(r"[^A-Z0-9]+")


def normaliser(texte: str) -> str:
    """Forme canonique d'un texte : « Arrêt ! » devient « ARRET »."""
    decompose = unicodedata.normalize("NFKD", texte)
    sans_accent = "".join(c for c in decompose if not unicodedata.combining(c))
    return _SIGNE.sub(" ", sans_accent.upper()).strip()


def _que_politesse(mots: tuple[str, ...]) -> bool:
    """Vrai si la suite de mots se découpe entièrement en formules de politesse."""
    if not mots:
        return True
    return any(
        mots[: len(formule)] == formule and _que_politesse(mots[len(formule) :])
        for formule in POLITESSE
    )


def correspond(texte: str, mot_cle: str) -> bool:
    """Vrai si le texte n'est que ce mot-clé, au plus entouré de politesse.

    Un mot-clé épelé lettre par lettre (« S T O P », « S.T.O.P ») est reconnu
    aussi : sans les espaces, le texte entier est alors le mot-clé."""
    canonique = normaliser(texte)
    cle = tuple(normaliser(mot_cle).split())
    if not cle:
        return False
    if canonique.replace(" ", "") == "".join(cle):
        return True
    mots = tuple(canonique.split())
    for debut in range(len(mots) - len(cle) + 1):
        fin = debut + len(cle)
        if mots[debut:fin] == cle and _que_politesse(mots[:debut]) and _que_politesse(mots[fin:]):
            return True
    return False


def reconnaitre(texte: str) -> MotCle | None:
    """Mot-clé de la plateforme dont le texte est la demande, ou None.

    Les mots-clés propres à un client viendront avec un mot-clé dédié par client,
    quand le numéro de destination suffira à le désigner."""
    for mot_cle in MotCle.objects.filter(client__isnull=True).order_by("mot_cle"):
        if correspond(texte, mot_cle.mot_cle):
            return mot_cle
    return None
