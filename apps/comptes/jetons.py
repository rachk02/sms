"""Liens signés à usage unique : invitation, vérification d'adresse et
réinitialisation du mot de passe.

Le jeton porte l'identifiant de l'utilisateur et une empreinte de son état
(mot de passe, date de vérification, courriel, activité). L'usage du lien
change cet état : un lien consommé ne fonctionne plus, sans qu'il faille
garder la trace des jetons émis. Sa durée est bornée par la signature.
"""

import hashlib
from datetime import timedelta

from django.core import signing

from .models import Utilisateur

INVITATION = ("comptes.invitation", timedelta(days=7))
VERIFICATION = ("comptes.verification", timedelta(hours=24))
# Mot de passe oublié (décision A2 du 9 novembre 2026) : une heure. Le lien
# tombe dès que le mot de passe change, l'empreinte le portant.
REINITIALISATION = ("comptes.reinitialisation", timedelta(hours=1))


class LienExpire(Exception):
    def __init__(self, utilisateur_id=None):
        self.utilisateur_id = utilisateur_id
        super().__init__("Lien expiré.")


class LienInvalide(Exception):
    pass


def _empreinte(utilisateur: Utilisateur) -> str:
    etat = "|".join(
        str(valeur)
        for valeur in (
            utilisateur.pk,
            utilisateur.password,
            utilisateur.courriel_verifie_le,
            utilisateur.courriel,
            utilisateur.is_active,
        )
    )
    return hashlib.sha256(etat.encode()).hexdigest()[:24]


def creer(utilisateur: Utilisateur, nature=INVITATION) -> str:
    sel, _ = nature
    return signing.dumps({"u": str(utilisateur.pk), "e": _empreinte(utilisateur)}, salt=sel)


def lire(jeton: str, nature=INVITATION) -> Utilisateur:
    """L'utilisateur du lien. Lève LienExpire (lien authentique mais trop
    ancien, pour proposer un renvoi) ou LienInvalide (falsifié ou consommé)."""
    sel, duree = nature
    try:
        donnees = signing.loads(jeton, salt=sel, max_age=duree)
    except signing.SignatureExpired:
        try:
            donnees = signing.loads(jeton, salt=sel)
        except signing.BadSignature:
            raise LienInvalide from None
        raise LienExpire(donnees.get("u")) from None
    except signing.BadSignature:
        raise LienInvalide from None
    utilisateur = Utilisateur.objects.filter(pk=donnees["u"]).first()
    if utilisateur is None or donnees["e"] != _empreinte(utilisateur):
        raise LienInvalide
    return utilisateur
