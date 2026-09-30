"""Lecture en flux des fichiers d'import, T-45."""

import io
import tracemalloc
import zipfile

import pytest

from apps.contacts.lecture import (
    FichierIllisible,
    lecteur_pour,
    lire_csv,
    lire_tableur,
    lire_texte,
)


def _csv(texte: str, encodage="utf-8") -> io.BytesIO:
    return io.BytesIO(texte.encode(encodage))


def test_un_csv_au_point_virgule_se_lit_ligne_a_ligne():
    lignes = list(lire_csv(_csv("numero;nom\n70 12 34 56;Awa\n+22671000000;Moussa\n")))
    assert lignes == [
        (1, ["numero", "nom"]),
        (2, ["70 12 34 56", "Awa"]),
        (3, ["+22671000000", "Moussa"]),
    ]


def test_un_csv_enregistre_depuis_un_tableur_en_windows_1252_se_lit():
    lignes = list(lire_csv(_csv("numero,ville\n70123456,Bobo-Dioulasso é\n", "cp1252")))
    assert lignes[1] == (2, ["70123456", "Bobo-Dioulasso é"])


def test_la_marque_d_encodage_utf8_ne_pollue_pas_la_premiere_cellule():
    lignes = list(lire_csv(io.BytesIO("﻿numero,nom\n".encode())))
    assert lignes[0] == (1, ["numero", "nom"])


def test_un_fichier_abime_au_milieu_s_arrete_en_nommant_la_ligne():
    propre = "".join(f"7012{rang:04d},Nom {rang}\n" for rang in range(5000))
    abime = propre.encode() + b"70999999,\xff\xfe\xfd\n" + b"70888888,apres\n"
    with pytest.raises(FichierIllisible) as erreur:
        list(lire_csv(io.BytesIO(abime)))
    assert erreur.value.numero_ligne == 5001


def test_des_octets_nuls_font_refuser_le_fichier():
    with pytest.raises(FichierIllisible, match="octets nuls"):
        list(lire_csv(io.BytesIO(b"numero\n70123456\x00\n")))


class _FluxGenere(io.RawIOBase):
    """Un CSV de N lignes produit à la demande : ni le test ni le lecteur ne
    tiennent le fichier entier en mémoire."""

    def __init__(self, lignes: int):
        self.lignes, self.rang, self.tampon = lignes, 0, b""

    def readable(self):
        return True

    def seekable(self):
        return True

    def seek(self, position, whence=0):
        self.rang, self.tampon = 0, b""
        return 0

    def readinto(self, cible):
        while len(self.tampon) < len(cible) and self.rang < self.lignes:
            bloc = "".join(
                f"+2267{n:07d},Prénom {n},Ville {n % 50}\n"
                for n in range(self.rang, min(self.rang + 1000, self.lignes))
            )
            self.rang = min(self.rang + 1000, self.lignes)
            self.tampon += bloc.encode()
        taille = min(len(cible), len(self.tampon))
        cible[:taille] = self.tampon[:taille]
        self.tampon = self.tampon[taille:]
        return taille


def _pic_memoire(lignes: int) -> tuple[int, int]:
    tracemalloc.start()
    total = 0
    for _ in lire_csv(io.BufferedReader(_FluxGenere(lignes), buffer_size=65536)):
        total += 1
    _, pic = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return total, pic


def test_deux_millions_de_lignes_se_lisent_a_memoire_constante():
    petites, pic_petit = _pic_memoire(200_000)
    grandes, pic_grand = _pic_memoire(2_000_000)
    assert (petites, grandes) == (200_000, 2_000_000)
    # Consigné pour la recette : le pic ne croît pas avec le nombre de lignes.
    print(
        f"pic mémoire : {pic_petit / 1024:.0f} Kio pour 200 000 lignes, "
        f"{pic_grand / 1024:.0f} Kio pour 2 000 000"
    )
    assert pic_grand < pic_petit * 1.5
    assert pic_grand < 2 * 1024 * 1024


def test_un_tableur_se_lit_en_lecture_seule():
    from openpyxl import Workbook

    classeur = Workbook(write_only=True)
    feuille = classeur.create_sheet()
    feuille.append(["numero", "nom"])
    feuille.append([70123456, "Awa"])
    feuille.append(["+22671000000", None])
    tampon = io.BytesIO()
    classeur.save(tampon)
    tampon.seek(0)
    assert list(lire_tableur(tampon)) == [
        (1, ["numero", "nom"]),
        (2, ["70123456", "Awa"]),
        (3, ["+22671000000"]),  # openpyxl omet les cellules vides en fin de ligne
    ]


def test_un_tableur_abime_est_refuse_proprement():
    with pytest.raises(FichierIllisible, match="classeur illisible"):
        list(lire_tableur(io.BytesIO(b"ceci n'est pas un classeur")))


def _docx(*paragraphes: str) -> io.BytesIO:
    corps = "".join(
        f'<w:p><w:r><w:t xml:space="preserve">{texte}</w:t></w:r></w:p>' for texte in paragraphes
    )
    document = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        f"<w:body>{corps}</w:body></w:document>"
    )
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w") as archive:
        archive.writestr("word/document.xml", document)
    tampon.seek(0)
    return tampon


def test_les_numeros_d_un_document_sont_extraits_par_motif():
    document = _docx(
        "Liste des membres de la coopérative",
        "Awa : 70 12 34 56, Moussa : +226 71.00.00.00",
        "Réunion le 12/09/2026 à 14 h",
        "Secrétariat 0022676543210",
    )
    assert [valeurs[0] for _, valeurs in lire_texte(document)] == [
        "70 12 34 56",
        "+226 71.00.00.00",
        "0022676543210",
    ]


def test_le_format_se_choisit_par_nom_et_un_format_inconnu_est_refuse():
    assert lecteur_pour("contacts.CSV") is lire_csv
    assert lecteur_pour("liste.xlsx") is lire_tableur
    with pytest.raises(FichierIllisible, match="formats admis"):
        lecteur_pour("contacts.pdf")
