"""Porte d'écriture du module fichiers : dépôt, lecture et liens signés.

Aucun fichier n'est servi par l'application : le client reçoit un lien signé,
valable quinze minutes, vers le stockage objet. Un lien qui fuite expire de
lui-même. Le type d'un fichier est reconnu à son contenu, jamais à son nom, et
sa taille bornée par le serveur. Les pièces d'identité sont chiffrées au repos
par le stockage, avec sa clé propre (chiffrement SSE-S3 de MinIO).

Un fichier lourd (import de contacts, jusqu'à 200 Mo) se dépose et se relit en
flux : il ne passe jamais tout entier en mémoire.
"""

import io
import shutil
import tempfile
import uuid
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone
from minio import Minio
from minio.sse import SseS3

from .models import Fichier

DUREE_LIEN = timedelta(minutes=15)
DEBUT = 8 * 1024


class FichierRefuse(ValueError):
    pass


@dataclass(frozen=True)
class TypeFichier:
    type_mime: str
    extension: str
    signature: bytes = b""
    # Pour une archive : un fichier qu'elle doit contenir.
    membre: str = ""
    # Pour un texte : aucune signature, seulement l'absence d'octet nul.
    texte: bool = False


PDF = TypeFichier("application/pdf", "pdf", b"%PDF-")
JPEG = TypeFichier("image/jpeg", "jpg", b"\xff\xd8\xff")
PNG = TypeFichier("image/png", "png", b"\x89PNG\r\n\x1a\n")
XLSX = TypeFichier(
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "xlsx",
    b"PK\x03\x04",
    membre="xl/workbook.xml",
)
DOCX = TypeFichier(
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "docx",
    b"PK\x03\x04",
    membre="word/document.xml",
)
CSV = TypeFichier("text/csv", "csv", texte=True)
# Un fichier texte ne commence jamais ainsi : PDF, images, exécutable Windows,
# ancien format bureautique, archive.
SIGNATURES_BINAIRES = (
    b"%PDF",
    b"\x89PNG",
    b"\xff\xd8\xff",
    b"GIF8",
    b"MZ",
    b"\xd0\xcf\x11\xe0",
    b"PK\x03\x04",
)

MO = 1024 * 1024
# Par famille : types admis, taille maximale, chiffrement au repos.
REGLES = {
    Fichier.Famille.PIECE: ((PDF, JPEG, PNG), 5 * MO, True),
    Fichier.Famille.IMPORT: ((XLSX, DOCX, CSV), 200 * MO, False),
    Fichier.Famille.EXPORT: ((CSV,), 200 * MO, False),
    # Bordereau de virement ou reçu d'espèces : pièce comptable, chiffrée.
    Fichier.Famille.PAIEMENT: ((PDF, JPEG, PNG), 5 * MO, True),
    Fichier.Famille.FACTURE: ((PDF,), 5 * MO, False),
}


@cache
def _stockage() -> Minio:
    config = settings.STOCKAGE_OBJET
    adresse = urlparse(config["point_acces"])
    client = Minio(
        adresse.netloc,
        access_key=config["identifiant"],
        secret_key=config["secret"],
        secure=adresse.scheme == "https",
    )
    if not client.bucket_exists(settings.MINIO_BUCKET):
        client.make_bucket(settings.MINIO_BUCKET)
    return client


def _attendus(admis) -> str:
    return ", ".join(t.extension.upper() for t in admis)


def reconnaitre(flux, admis: tuple[TypeFichier, ...]) -> TypeFichier:
    """Reconnaît le type au contenu. `flux` : un fichier binaire positionnable,
    ou des octets."""
    if isinstance(flux, bytes):
        flux = io.BytesIO(flux)
    debut = flux.read(DEBUT)
    flux.seek(0)
    for type_fichier in admis:
        if type_fichier.texte:
            if debut and b"\x00" not in debut and not debut.startswith(SIGNATURES_BINAIRES):
                return type_fichier
            continue
        if not debut.startswith(type_fichier.signature):
            continue
        if type_fichier.membre:
            try:
                with zipfile.ZipFile(flux) as archive:
                    present = type_fichier.membre in archive.namelist()
            except zipfile.BadZipFile:
                present = False
            flux.seek(0)
            if not present:
                continue
        return type_fichier
    raise FichierRefuse(
        f"Type de fichier non admis : {_attendus(admis)} attendu, reconnu au contenu."
    )


def deposer(*, client, famille: str, contenu=None, flux=None, nom_origine: str) -> Fichier:
    """Vérifie, dépose et enregistre un fichier, donné en octets (`contenu`) ou
    en flux binaire positionnable (`flux`). Lève FichierRefuse avant tout dépôt
    si le contenu ou la taille ne conviennent pas."""
    try:
        admis, taille_max, chiffre = REGLES[famille]
    except KeyError:
        raise FichierRefuse(f"Famille de fichiers non prise en charge : {famille}") from None
    if flux is None:
        flux = io.BytesIO(contenu or b"")
    taille = flux.seek(0, io.SEEK_END)
    flux.seek(0)
    if taille > taille_max:
        raise FichierRefuse(f"Fichier trop lourd : {taille_max // MO} Mo au plus.")
    if not taille:
        raise FichierRefuse("Fichier vide.")
    type_fichier = reconnaitre(flux, admis)
    chemin = f"clients/{client.id}/{famille.lower()}/{uuid.uuid4()}.{type_fichier.extension}"
    _stockage().put_object(
        settings.MINIO_BUCKET,
        chemin,
        flux,
        length=taille,
        content_type=type_fichier.type_mime,
        sse=SseS3() if chiffre else None,
        part_size=10 * MO,
    )
    return Fichier.objects.create(
        client=client,
        famille=famille,
        chemin=chemin,
        nom_origine=nom_origine[:255],
        type_mime=type_fichier.type_mime,
        taille_octets=taille,
        chiffre=chiffre,
    )


def extension(fichier: Fichier) -> str:
    return fichier.chemin.rsplit(".", 1)[-1]


def ouvrir(fichier: Fichier):
    """Copie le fichier du stockage dans un fichier temporaire sur disque, par
    morceaux, et le rend ouvert en lecture binaire : positionnable, comme les
    lecteurs de tableur l'exigent, sans passer tout entier en mémoire. Le
    fichier temporaire disparaît à sa fermeture."""
    temporaire = tempfile.TemporaryFile()
    reponse = _stockage().get_object(settings.MINIO_BUCKET, fichier.chemin)
    try:
        shutil.copyfileobj(reponse, temporaire, length=MO)
    finally:
        reponse.close()
        reponse.release_conn()
    temporaire.seek(0)
    return temporaire


def lien(fichier: Fichier, duree: timedelta = DUREE_LIEN) -> str:
    """Lien signé vers le fichier, à produire après contrôle des droits."""
    return _stockage().presigned_get_object(settings.MINIO_BUCKET, fichier.chemin, expires=duree)


def supprimer(fichier: Fichier) -> None:
    """Efface le contenu du stockage et note la suppression. La ligne reste, sans
    contenu derrière elle : la trace de ce qui a existé, rien de plus."""
    if fichier.supprime_le is None:
        _stockage().remove_object(settings.MINIO_BUCKET, fichier.chemin)
        Fichier.objects.filter(id=fichier.id).update(supprime_le=timezone.now())
