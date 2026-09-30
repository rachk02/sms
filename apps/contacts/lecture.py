"""Lecture en flux des fichiers d'import.

Un fichier peut compter deux millions de lignes : il n'est jamais chargé en
mémoire. Chaque lecteur rend les lignes une à une, avec leur numéro, sous la
forme d'une liste de valeurs texte. Un fichier abîmé arrête la lecture sur une
erreur qui nomme la ligne fautive, jamais sur un plantage.

- CSV : ligne à ligne, séparateur deviné sur le début du fichier, encodage
  UTF-8 (avec ou sans marque) ou, à défaut, Windows-1252, fréquent pour les
  fichiers enregistrés depuis un tableur.
- Tableur (.xlsx) : mode lecture seule, qui parcourt la feuille sans la charger.
- Traitement de texte (.docx) : les paragraphes sont lus un à un dans l'archive
  et les numéros de téléphone y sont repérés par motif.
"""

import codecs
import csv
import re
import zipfile
from collections.abc import Iterator
from xml.etree.ElementTree import iterparse

ECHANTILLON = 64 * 1024
SEPARATEURS = ",;\t|"
NAMESPACE_WORD = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
# Un numéro burkinabè dans un texte libre : indicatif facultatif, huit chiffres,
# séparateurs tolérés. La normalisation stricte vient ensuite.
MOTIF_NUMERO = re.compile(r"(?<![\d+])(?:(?:\+|00)\s*226[\s.\-]*)?(?:\d[\s.\-]?){7}\d(?!\d)")

Ligne = tuple[int, list[str]]


class FichierIllisible(ValueError):
    def __init__(self, numero_ligne: int, motif: str):
        self.numero_ligne = numero_ligne
        self.motif = motif
        super().__init__(f"Fichier illisible à la ligne {numero_ligne} : {motif}")


def _encodage(debut: bytes, complet: bool) -> str:
    """UTF-8 si l'échantillon se lit ainsi, Windows-1252 sinon. Un échantillon
    coupé en plein caractère n'est pas une erreur, sauf s'il est tout le fichier."""
    if debut.startswith(codecs.BOM_UTF8):
        return "utf-8-sig"
    try:
        codecs.getincrementaldecoder("utf-8")().decode(debut, final=complet)
    except UnicodeDecodeError:
        return "cp1252"
    return "utf-8"


def _lignes_decodees(flux, encodage: str, compteur: list[int]) -> Iterator[str]:
    """Décode ligne à ligne, pour qu'une erreur nomme sa ligne exacte : un
    décodeur de flux lirait en avance et se tromperait de ligne."""
    for brute in iter(flux.readline, b""):
        compteur[0] += 1
        try:
            yield brute.decode(encodage)
        except UnicodeDecodeError:
            raise FichierIllisible(compteur[0], f"caractères illisibles en {encodage}") from None


def lire_csv(flux) -> Iterator[Ligne]:
    """`flux` : un fichier ouvert en binaire."""
    debut = flux.read(ECHANTILLON)
    flux.seek(0)
    encodage = _encodage(debut, complet=len(debut) < ECHANTILLON)
    try:
        echantillon = debut.decode(encodage, errors="ignore")
        dialecte = csv.Sniffer().sniff(echantillon, delimiters=SEPARATEURS)
    except csv.Error:
        dialecte = csv.excel
    compteur = [0]
    lecteur = csv.reader(_lignes_decodees(flux, encodage, compteur), dialecte)
    try:
        for valeurs in lecteur:
            if "\x00" in "".join(valeurs):
                raise FichierIllisible(
                    lecteur.line_num, "octets nuls, le fichier n'est pas un texte"
                )
            yield lecteur.line_num, [v.strip() for v in valeurs]
    except csv.Error as erreur:
        raise FichierIllisible(lecteur.line_num, str(erreur)) from None


def lire_tableur(flux) -> Iterator[Ligne]:
    """Première feuille d'un classeur .xlsx, en lecture seule."""
    from openpyxl import load_workbook

    try:
        classeur = load_workbook(flux, read_only=True, data_only=True)
    except (zipfile.BadZipFile, KeyError, OSError) as erreur:
        raise FichierIllisible(1, f"classeur illisible ({erreur})") from None
    try:
        feuille = classeur.worksheets[0]
        for numero, valeurs in enumerate(feuille.iter_rows(values_only=True), start=1):
            yield numero, ["" if v is None else _texte_cellule(v) for v in valeurs]
    finally:
        classeur.close()


def _texte_cellule(valeur) -> str:
    # Un numéro saisi dans une cellule numérique revient en entier ou en flottant.
    if isinstance(valeur, float) and valeur.is_integer():
        return str(int(valeur))
    return str(valeur).strip()


def lire_texte(flux) -> Iterator[Ligne]:
    """Un document .docx : une ligne par numéro repéré, numérotée par paragraphe."""
    try:
        archive = zipfile.ZipFile(flux)
        document = archive.open("word/document.xml")
    except (zipfile.BadZipFile, KeyError) as erreur:
        raise FichierIllisible(1, f"document illisible ({erreur})") from None
    paragraphe, morceaux = 0, []
    try:
        for _, element in iterparse(document, events=("end",)):
            if element.tag == f"{NAMESPACE_WORD}t" and element.text:
                morceaux.append(element.text)
            elif element.tag == f"{NAMESPACE_WORD}p":
                paragraphe += 1
                for trouve in MOTIF_NUMERO.finditer("".join(morceaux)):
                    yield paragraphe, [trouve.group(0)]
                morceaux = []
                element.clear()
    except SyntaxError as erreur:
        raise FichierIllisible(paragraphe + 1, f"XML invalide ({erreur})") from None
    finally:
        document.close()
        archive.close()


LECTEURS = {"csv": lire_csv, "txt": lire_csv, "xlsx": lire_tableur, "docx": lire_texte}


def lecteur_pour(nom_fichier: str):
    extension = nom_fichier.rsplit(".", 1)[-1].lower() if "." in nom_fichier else ""
    try:
        return LECTEURS[extension]
    except KeyError:
        admis = ", ".join(sorted(LECTEURS))
        raise FichierIllisible(0, f"format non pris en charge, formats admis : {admis}") from None
