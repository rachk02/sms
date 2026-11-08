"""Archives bureautiques déposées (.xlsx, .docx) : bornées une fois décompressées."""

import io
import zipfile

import pytest

from apps.fichiers import services


def _classeur(octets_feuille: int) -> bytes:
    tampon = io.BytesIO()
    with zipfile.ZipFile(tampon, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("xl/workbook.xml", "<workbook/>")
        archive.writestr("xl/worksheets/sheet1.xml", b"0" * octets_feuille)
    return tampon.getvalue()


def test_une_archive_qui_se_decompresse_au_dela_de_la_borne_est_refusee(monkeypatch):
    """Quelques kilo-octets qui se décompressent en plusieurs mégaoctets : la
    taille déposée ne dit rien de ce que la lecture devra traiter. L'archive est
    refusée au dépôt dès que ses membres dépassent la borne."""
    monkeypatch.setattr(services, "DECOMPRESSE_MAX", 4 * services.MO, raising=False)
    contenu = _classeur(5 * services.MO)
    assert len(contenu) < 100_000
    with pytest.raises(services.FichierRefuse, match="décompress"):
        services.reconnaitre(contenu, (services.XLSX,))
    # Sous la borne, la même archive passe.
    monkeypatch.setattr(services, "DECOMPRESSE_MAX", 8 * services.MO)
    assert services.reconnaitre(contenu, (services.XLSX,)) is services.XLSX
