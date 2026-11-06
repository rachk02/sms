"""Écriture d'un PDF d'une page, sans bibliothèque extérieure.

Assez pour une facture : du texte en Helvetica et Helvetica gras, placé ligne
par ligne, et des filets. Les polices de base du PDF, en codage WinAnsi
(cp1252), couvrent le français, y compris œ, Œ, l'apostrophe typographique et
les guillemets ; un caractère hors de cette table devient « ? ». Rien n'est
compressé : la facture reste lisible telle quelle, et pèse quelques kilo-octets.
"""

from dataclasses import dataclass

LARGEUR, HAUTEUR = 595, 842  # A4, en points


@dataclass(frozen=True)
class Texte:
    x: float
    y: float
    texte: str
    taille: float = 10
    gras: bool = False


@dataclass(frozen=True)
class Filet:
    x1: float
    y1: float
    x2: float
    y2: float


def _chaine(texte: str) -> bytes:
    brut = texte.encode("cp1252", errors="replace")
    return b"(" + brut.replace(b"\\", b"\\\\").replace(b"(", b"\\(").replace(b")", b"\\)") + b")"


def _contenu(elements) -> bytes:
    morceaux = []
    for element in elements:
        if isinstance(element, Texte):
            police = b"/F2" if element.gras else b"/F1"
            morceaux.append(
                b"BT %s %g Tf %g %g Td %s Tj ET"
                % (police, element.taille, element.x, element.y, _chaine(element.texte))
            )
        else:
            morceaux.append(
                b"0.5 w %g %g m %g %g l S" % (element.x1, element.y1, element.x2, element.y2)
            )
    return b"\n".join(morceaux)


def _police(nom: bytes) -> bytes:
    return b"<< /Type /Font /Subtype /Type1 /BaseFont /%s /Encoding /WinAnsiEncoding >>" % nom


def page(elements, *, titre: str = "") -> bytes:
    """Le PDF d'une page A4 portant ces éléments."""
    contenu = _contenu(elements)
    objets = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %d %d] "
        b"/Resources << /Font << /F1 4 0 R /F2 5 0 R >> >> /Contents 6 0 R >>" % (LARGEUR, HAUTEUR),
        _police(b"Helvetica"),
        _police(b"Helvetica-Bold"),
        b"<< /Length %d >>\nstream\n%s\nendstream" % (len(contenu), contenu),
        b"<< /Title %s /Producer (EL-IMMEKA SMS) >>" % _chaine(titre),
    ]
    sortie = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    positions = []
    for numero, objet in enumerate(objets, start=1):
        positions.append(len(sortie))
        sortie += b"%d 0 obj\n%s\nendobj\n" % (numero, objet)
    debut_table = len(sortie)
    sortie += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objets) + 1)
    for position in positions:
        sortie += b"%010d 00000 n \n" % position
    sortie += b"trailer\n<< /Size %d /Root 1 0 R /Info %d 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objets) + 1,
        len(objets),
        debut_table,
    )
    return bytes(sortie)
