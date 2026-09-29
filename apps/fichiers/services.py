"""Porte d'écriture du module fichiers : dépôt et liens signés.

Aucun fichier n'est servi par l'application : le client reçoit un lien signé,
valable quinze minutes, vers le stockage objet. Un lien qui fuite expire de
lui-même. Le type d'un fichier est reconnu à son contenu, jamais à son nom, et
sa taille bornée par le serveur. Les pièces d'identité sont chiffrées au repos
par le stockage, avec sa clé propre (chiffrement SSE-S3 de MinIO).
"""

import io
import uuid
from dataclasses import dataclass
from datetime import timedelta
from functools import cache
from urllib.parse import urlparse

from django.conf import settings
from minio import Minio
from minio.sse import SseS3

from .models import Fichier

DUREE_LIEN = timedelta(minutes=15)


class FichierRefuse(ValueError):
    pass


@dataclass(frozen=True)
class TypeFichier:
    type_mime: str
    extension: str
    signature: bytes


PDF = TypeFichier("application/pdf", "pdf", b"%PDF-")
JPEG = TypeFichier("image/jpeg", "jpg", b"\xff\xd8\xff")
PNG = TypeFichier("image/png", "png", b"\x89PNG\r\n\x1a\n")

# Par famille : types admis, taille maximale, chiffrement au repos.
REGLES = {
    Fichier.Famille.PIECE: ((PDF, JPEG, PNG), 5 * 1024 * 1024, True),
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


def reconnaitre(contenu: bytes, admis: tuple[TypeFichier, ...]) -> TypeFichier:
    for type_fichier in admis:
        if contenu.startswith(type_fichier.signature):
            return type_fichier
    attendus = ", ".join(t.extension.upper() for t in admis)
    raise FichierRefuse(f"Type de fichier non admis : {attendus} attendu, reconnu au contenu.")


def deposer(*, client, famille: str, contenu: bytes, nom_origine: str) -> Fichier:
    """Vérifie, dépose et enregistre un fichier. Lève FichierRefuse avant tout
    dépôt si le contenu ou la taille ne conviennent pas."""
    try:
        admis, taille_max, chiffre = REGLES[famille]
    except KeyError:
        raise FichierRefuse(f"Famille de fichiers non prise en charge : {famille}") from None
    if len(contenu) > taille_max:
        raise FichierRefuse(f"Fichier trop lourd : {taille_max // (1024 * 1024)} Mo au plus.")
    if not contenu:
        raise FichierRefuse("Fichier vide.")
    type_fichier = reconnaitre(contenu, admis)
    chemin = f"clients/{client.id}/{famille.lower()}/{uuid.uuid4()}.{type_fichier.extension}"
    _stockage().put_object(
        settings.MINIO_BUCKET,
        chemin,
        io.BytesIO(contenu),
        length=len(contenu),
        content_type=type_fichier.type_mime,
        sse=SseS3() if chiffre else None,
    )
    return Fichier.objects.create(
        client=client,
        famille=famille,
        chemin=chemin,
        nom_origine=nom_origine[:255],
        type_mime=type_fichier.type_mime,
        taille_octets=len(contenu),
        chiffre=chiffre,
    )


def lien(fichier: Fichier, duree: timedelta = DUREE_LIEN) -> str:
    """Lien signé vers le fichier, à produire après contrôle des droits."""
    return _stockage().presigned_get_object(settings.MINIO_BUCKET, fichier.chemin, expires=duree)
