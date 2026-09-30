"""Second facteur : application d'authentification (TOTP) et codes de secours.

L'application d'authentification est la méthode par défaut : un code par SMS
dépendrait de la plateforme elle-même, et une panne d'envoi fermerait alors
l'accès à ceux qui doivent la diagnostiquer. Le second facteur est obligatoire
pour les rôles d'administration (administrateur d'un compte, agent EL-IMMEKA)
passé un délai de grâce ; il est facultatif pour les autres.
"""

import secrets
from datetime import timedelta

import pyotp
import segno
from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from apps.core.chiffrement import chiffrer, dechiffrer

from .models import CodeSecours, Utilisateur

EMETTEUR = "EL-IMMEKA SMS"
DELAI_DE_GRACE = timedelta(days=7)
NOMBRE_CODES_SECOURS = 10
ALPHABET_SECOURS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # sans 0, O, 1, I


class CodeRefuse(ValueError):
    pass


def exige(utilisateur: Utilisateur) -> bool:
    return utilisateur.is_staff or utilisateur.role == Utilisateur.Role.ADMIN


def delai_de_grace_ecoule(utilisateur: Utilisateur) -> bool:
    return timezone.now() >= utilisateur.cree_le + DELAI_DE_GRACE


def nouveau_secret() -> str:
    return pyotp.random_base32()


def adresse(utilisateur: Utilisateur, secret: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=utilisateur.courriel, issuer_name=EMETTEUR)


def qr_svg(uri: str) -> str:
    return segno.make(uri, error="m").svg_inline(scale=5, dark="#0f172a")


def _pas_valide(secret: str, code: str, dernier_pas: int | None) -> int | None:
    """Pas de temps du code s'il est valable (fenêtre d'un pas de chaque côté,
    pour une horloge un peu décalée) et plus récent que le dernier accepté."""
    totp = pyotp.TOTP(secret)
    maintenant = timezone.now()
    for decalage in (-1, 0, 1):
        instant = maintenant + timedelta(seconds=decalage * totp.interval)
        pas = totp.timecode(instant)
        if secrets.compare_digest(totp.at(instant), code):
            if dernier_pas is not None and pas <= dernier_pas:
                return None  # code déjà servi : un rejeu ne passe pas
            return pas
    return None


def _normaliser_code(code: str) -> str:
    return "".join(code.split()).upper().replace("-", "")


def _generer_codes() -> list[str]:
    return [
        "".join(secrets.choice(ALPHABET_SECOURS) for _ in range(8))
        for _ in range(NOMBRE_CODES_SECOURS)
    ]


def presenter(code: str) -> str:
    return f"{code[:4]}-{code[4:]}"


@transaction.atomic
def activer(utilisateur: Utilisateur, secret: str, code: str) -> list[str]:
    """Active le second facteur si le code prouve que l'application est réglée.
    Renvoie les codes de secours, en clair pour la seule et unique fois."""
    pas = _pas_valide(secret, _normaliser_code(code), None)
    if pas is None:
        raise CodeRefuse("Code invalide : vérifiez l'heure de votre téléphone et réessayez.")
    Utilisateur.objects.filter(pk=utilisateur.pk).update(
        second_facteur_actif=True,
        second_facteur_secret=chiffrer(secret),
        second_facteur_dernier_pas=pas,
        modifie_le=timezone.now(),
    )
    codes = _generer_codes()
    CodeSecours.objects.filter(utilisateur=utilisateur).delete()
    CodeSecours.objects.bulk_create(
        CodeSecours(utilisateur=utilisateur, condensat=make_password(c)) for c in codes
    )
    return [presenter(c) for c in codes]


@transaction.atomic
def verifier(utilisateur: Utilisateur, code: str) -> bool:
    """Vrai si le code de l'application, ou un code de secours encore inutilisé,
    est valable. Un code de secours servi est consommé."""
    courant = Utilisateur.objects.select_for_update().get(pk=utilisateur.pk)
    if not courant.second_facteur_actif:
        return False
    code = _normaliser_code(code)
    if len(code) == 6 and code.isdigit():
        secret = dechiffrer(courant.second_facteur_secret)
        pas = _pas_valide(secret, code, courant.second_facteur_dernier_pas)
        if pas is None:
            return False
        Utilisateur.objects.filter(pk=courant.pk).update(second_facteur_dernier_pas=pas)
        return True
    for secours in CodeSecours.objects.select_for_update().filter(
        utilisateur=courant, utilise_le__isnull=True
    ):
        if check_password(code, secours.condensat):
            secours.utilise_le = timezone.now()
            secours.save(update_fields=["utilise_le"])
            return True
    return False


@transaction.atomic
def reinitialiser(
    utilisateur: Utilisateur, *, agent, motif: str, identite_verifiee: bool, adresse_ip=None
):
    """Retire le second facteur d'un utilisateur qui l'a perdu. Réservé à un
    agent, après vérification de l'identité par un canal indépendant : jamais
    sur simple courriel. Tracé au journal d'audit, et l'utilisateur prévenu."""
    from apps.core.audit import auditer
    from apps.core.courriels import envoyer

    if not identite_verifiee:
        raise CodeRefuse("L'identité du demandeur doit être vérifiée par un canal indépendant.")
    if not motif.strip():
        raise CodeRefuse("Une réinitialisation exige un motif.")
    Utilisateur.objects.filter(pk=utilisateur.pk).update(
        second_facteur_actif=False,
        second_facteur_secret="",
        second_facteur_dernier_pas=None,
        modifie_le=timezone.now(),
    )
    CodeSecours.objects.filter(utilisateur=utilisateur).delete()
    auditer(
        "comptes.second_facteur.reinitialise",
        utilisateur,
        auteur=agent,
        client=utilisateur.client,
        avant={"second_facteur_actif": True},
        apres={"second_facteur_actif": False},
        motif=motif.strip(),
        adresse_ip=adresse_ip,
    )
    envoyer(
        utilisateur.courriel,
        "Votre second facteur a été réinitialisé",
        f"Bonjour {utilisateur.nom},\n\nÀ votre demande, votre second facteur a été retiré par "
        "l'équipe EL-IMMEKA. Réglez-le de nouveau à votre prochaine connexion. Si vous "
        "n'êtes pas à l'origine de cette demande, contactez-nous sans attendre.",
    )
