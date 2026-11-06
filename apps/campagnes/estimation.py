"""Nombre de segments et coût annoncés, au cas le plus long (T-56).

La personnalisation change la longueur de chaque message. Le compteur retient
le destinataire qui produit le message le plus long, jamais la moyenne, et le
coût annoncé applique ce nombre de segments à tous : il est toujours supérieur
ou égal au coût réel, jamais inférieur. Un seul caractère hors table GSM chez
un seul destinataire fait basculer l'annonce en UCS-2.
"""

from dataclasses import dataclass

from apps.core.encodage import Encodage, analyser
from apps.credits.tarifs import prix_segment

from .personnalisation import Gabarit

CHAMPS_LUS = (
    "numero_normalise",
    "nom",
    "prenom",
    "ville",
    "champs",
    "operateur_presume",
    "operateur_confirme",
)


@dataclass(frozen=True)
class Estimation:
    destinataires: int
    encodage: Encodage
    segments: int
    unites: int
    cout: int
    # Le message le plus long, montré au client comme exemple du cas annoncé.
    exemple: str


def estimer(client, gabarit: Gabarit, contacts, prix: dict | None = None) -> Estimation:
    """`contacts` : un queryset de contacts, lu en flux. `prix` : le prix d'un
    segment par réseau, figé par l'appelant ; à défaut, le prix du moment."""
    destinataires, unites_max, exemple = 0, 0, ""
    segments_max, ucs2 = 1, False
    par_operateur: dict[str, int] = {}
    for contact in contacts.only(*CHAMPS_LUS).iterator(chunk_size=2000):
        texte = gabarit.rendre(contact)
        analyse = analyser(texte)
        destinataires += 1
        operateur = contact.operateur_confirme or contact.operateur_presume
        par_operateur[operateur] = par_operateur.get(operateur, 0) + 1
        ucs2 = ucs2 or analyse.encodage == Encodage.UCS2
        if (analyse.segments, analyse.unites) > (segments_max, unites_max):
            segments_max, unites_max, exemple = analyse.segments, analyse.unites, texte
    cout = sum(
        nombre * segments_max * ((prix or {}).get(operateur) or prix_segment(client.id, operateur))
        for operateur, nombre in par_operateur.items()
    )
    return Estimation(
        destinataires=destinataires,
        encodage=Encodage.UCS2 if ucs2 else Encodage.GSM7,
        segments=segments_max,
        unites=unites_max,
        cout=cout,
        exemple=exemple,
    )
