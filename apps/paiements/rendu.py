"""Mise en page d'une facture ou d'un avoir, à partir de ses mentions figées.

Fonction pure : les mêmes mentions donnent toujours le même PDF. Aucune lecture
en base, aucune valeur prise ailleurs que dans la facture émise.
"""

from .pdf import Filet, Texte, page

GAUCHE, DROITE = 50, 545


def montant(valeur: int) -> str:
    return f"{valeur:,}".replace(",", " ") + " FCFA"


def _lignes(x, y, lignes):
    return [Texte(x, y - 13 * rang, ligne, 9) for rang, ligne in enumerate(lignes)]


def facture_pdf(mentions: dict) -> bytes:
    emetteur, client = mentions["emetteur"], mentions["client"]
    elements = [
        Texte(GAUCHE, 790, emetteur["raison_sociale"], 14, gras=True),
        *_lignes(
            GAUCHE,
            772,
            [
                emetteur["adresse"],
                f"RCCM : {emetteur['rccm']}",
                f"IFU : {emetteur['ifu']}",
                f"Régime fiscal : {emetteur['regime_fiscal']}",
            ],
        ),
        Texte(350, 790, f"{mentions['titre']} n° {mentions['numero']}", 13, gras=True),
        Texte(350, 772, f"Émise le {mentions['emise_le']}", 9),
    ]
    lignes_client = [client["raison_sociale"]]
    if client.get("rccm"):
        lignes_client.append(f"RCCM : {client['rccm']}")
    if client.get("ifu"):
        lignes_client.append(f"IFU : {client['ifu']}")
    elements += [Texte(350, 730, "Client", 9, gras=True), *_lignes(350, 717, lignes_client)]

    y = 640
    if mentions.get("origine"):
        origine = mentions["origine"]
        elements.append(
            Texte(
                GAUCHE,
                y,
                f"Avoir sur la facture n° {origine['numero']} du {origine['emise_le']}.",
                9,
            )
        )
        elements.append(Texte(GAUCHE, y - 13, f"Motif : {mentions['motif']}", 9))
        y -= 40

    elements += [
        Texte(GAUCHE, y, "Désignation", 9, gras=True),
        Texte(300, y, "Quantité", 9, gras=True),
        Texte(370, y, "Prix unitaire HT", 9, gras=True),
        Texte(470, y, "Montant HT", 9, gras=True),
        Filet(GAUCHE, y - 5, DROITE, y - 5),
    ]
    for ligne in mentions["lignes"]:
        y -= 20
        elements += [
            Texte(GAUCHE, y, ligne["designation"], 9),
            Texte(300, y, f"{ligne['quantite']:,}".replace(",", " "), 9),
            Texte(370, y, montant(ligne["prix_unitaire_ht"]), 9),
            Texte(470, y, montant(ligne["montant_ht"]), 9),
        ]
    y -= 15
    elements.append(Filet(GAUCHE, y, DROITE, y))
    tva = f"TVA {mentions['taux_tva']} %" if mentions["tva_active"] else "TVA non appliquée"
    for libelle, valeur, gras in (
        ("Total hors taxes", mentions["montant_ht"], False),
        (tva, mentions["tva"], False),
        ("Total toutes taxes comprises", mentions["montant_ttc"], True),
    ):
        y -= 18
        elements += [Texte(330, y, libelle, 10, gras), Texte(470, y, montant(valeur), 10, gras)]

    paiement = mentions.get("paiement")
    if paiement:
        y -= 40
        elements.append(
            Texte(
                GAUCHE,
                y,
                f"Facture acquittée : payée le {paiement['le']} par {paiement['moyen']}, "
                f"référence {paiement['reference']}.",
                9,
            )
        )
    return page(elements, titre=f"{mentions['titre']} {mentions['numero']}")
