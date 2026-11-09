"""Authentification par courriel et mot de passe, avec verrouillage.

Branchée comme moteur d'authentification de Django : le portail des clients et
l'administration du back-office passent par elle, si bien que le verrouillage
et le journal des connexions valent pour les deux.

Cinq échecs sur un même courriel le verrouillent quinze minutes. Le compteur
porte sur le courriel saisi, qu'il existe ou non : une adresse inconnue se
verrouille comme une autre, et rien ne révèle si un compte existe. Il vit dans
Redis ; pour un compte existant, l'état est aussi porté en base
(`echecs_consecutifs`, `verrouille_jusqu_a`), où le back-office le voit.

En plus, vingt échecs en quinze minutes depuis une même adresse IP la freinent
quinze minutes, quel que soit le courriel essayé (décision A3 du 9 novembre
2026) : le verrouillage par courriel n'arrête pas celui qui essaie un mot de
passe sur mille adresses. Une connexion réussie ne remet pas ce compteur à
zéro, sans quoi un compte valide suffirait à le vider. L'adresse se lit comme
pour les rappels de paiement (`apps.core.reseau.adresse_source`).
"""

import hashlib
import ipaddress
import logging
from datetime import timedelta

from django.contrib.auth.backends import ModelBackend
from django.contrib.auth.hashers import make_password
from django.utils import timezone

from apps.core.redis import client_redis
from apps.core.reseau import adresse_source

from .models import Connexion, GestionnaireUtilisateur, Utilisateur

journal = logging.getLogger(__name__)

MAX_ECHECS = 5
DUREE_VERROUILLAGE = timedelta(minutes=15)
CLE = "sms:connexion:echecs:{empreinte}"
MAX_ECHECS_IP = 20
DUREE_FREINAGE_IP = timedelta(minutes=15)
CLE_IP = "sms:connexion:ip:{adresse}"


def _cle(courriel: str) -> str:
    empreinte = hashlib.sha256(courriel.encode()).hexdigest()[:32]
    return CLE.format(empreinte=empreinte)


def _cle_ip(adresse: str) -> str:
    return CLE_IP.format(adresse=adresse)


def ip_freinee(adresse: str) -> bool:
    if not adresse:
        return False
    return int(client_redis().get(_cle_ip(adresse)) or 0) >= MAX_ECHECS_IP


def _noter_echec_ip(adresse: str) -> None:
    """Fenêtre de quinze minutes ouverte au premier échec ; au vingtième, le
    blocage repart pour quinze minutes pleines."""
    if not adresse:
        return
    cle = _cle_ip(adresse)
    duree = int(DUREE_FREINAGE_IP.total_seconds())
    redis = client_redis()
    with redis.pipeline() as lot:
        lot.incr(cle)
        lot.expire(cle, duree, nx=True)
        echecs = lot.execute()[0]
    if echecs == MAX_ECHECS_IP:
        redis.expire(cle, duree)


def _adresse(request) -> str:
    if request is None or not hasattr(request, "META"):
        return ""
    return adresse_source(request)


def _ip_valide(adresse: str) -> str | None:
    try:
        return str(ipaddress.ip_address(adresse))
    except ValueError:
        return None


def normaliser(courriel: str | None) -> str:
    return GestionnaireUtilisateur.normaliser_courriel(courriel or "")


def verrouille(courriel: str) -> bool:
    echecs = client_redis().get(_cle(normaliser(courriel)))
    return int(echecs or 0) >= MAX_ECHECS


def _noter_echec(courriel: str, utilisateur: Utilisateur | None) -> None:
    cle = _cle(courriel)
    redis = client_redis()
    with redis.pipeline() as lot:
        lot.incr(cle)
        lot.expire(cle, int(DUREE_VERROUILLAGE.total_seconds()), nx=True)
        echecs = lot.execute()[0]
    if utilisateur is not None:
        champs = {"echecs_consecutifs": min(echecs, 32_767)}
        if echecs >= MAX_ECHECS:
            champs["verrouille_jusqu_a"] = timezone.now() + DUREE_VERROUILLAGE
        Utilisateur.objects.filter(pk=utilisateur.pk).update(**champs)


def _noter_reussite(courriel: str, utilisateur: Utilisateur) -> None:
    client_redis().delete(_cle(courriel))
    Utilisateur.objects.filter(pk=utilisateur.pk).update(
        echecs_consecutifs=0, verrouille_jusqu_a=None
    )


class CompteVerrouille(Exception):
    pass


def controler_mot_de_passe(utilisateur: Utilisateur, mot_de_passe: str) -> bool:
    """Vrai si le mot de passe est celui de l'utilisateur. Sert à confirmer le
    mot de passe actuel d'une personne déjà connectée : un essai faux compte
    dans le même verrouillage que la connexion, et un compte verrouillé lève
    CompteVerrouille sans contrôler le mot de passe."""
    courriel = normaliser(utilisateur.courriel)
    if verrouille(courriel):
        raise CompteVerrouille
    if utilisateur.check_password(mot_de_passe):
        _noter_reussite(courriel, utilisateur)
        return True
    _noter_echec(courriel, utilisateur)
    return False


def _journaliser(request, courriel, utilisateur, reussie, motif=""):
    meta = getattr(request, "META", {}) if request is not None else {}
    Connexion.objects.create(
        utilisateur=utilisateur,
        courriel_saisi=courriel[:254],
        reussie=reussie,
        motif=motif,
        adresse_ip=_ip_valide(_adresse(request)),
        agent_utilisateur=meta.get("HTTP_USER_AGENT", "")[:300],
    )


class AuthentificationVerrouillee(ModelBackend):
    def authenticate(self, request, username=None, password=None, courriel=None, **kwargs):
        courriel = normaliser(courriel or username)
        if not courriel or password is None:
            return None
        adresse = _adresse(request)
        if ip_freinee(adresse):
            _journaliser(request, courriel, None, False, "IP_FREINEE")
            return None
        if verrouille(courriel):
            _noter_echec_ip(adresse)
            _journaliser(request, courriel, None, False, "VERROUILLE")
            return None
        utilisateur = Utilisateur.objects.filter(courriel=courriel).first()
        if utilisateur is None:
            make_password(password)  # même durée qu'un vrai contrôle
            _noter_echec(courriel, None)
            _noter_echec_ip(adresse)
            _journaliser(request, courriel, None, False, "INCONNU")
            return None
        if not utilisateur.check_password(password) or not self.user_can_authenticate(utilisateur):
            _noter_echec(courriel, utilisateur)
            _noter_echec_ip(adresse)
            _journaliser(request, courriel, utilisateur, False, "MOT_DE_PASSE")
            return None
        if utilisateur.courriel_verifie_le is None and not utilisateur.is_staff:
            # Un compte client ne sert qu'une fois son adresse vérifiée (T-32).
            _journaliser(request, courriel, utilisateur, False, "COURRIEL_NON_VERIFIE")
            return None
        _noter_reussite(courriel, utilisateur)
        _journaliser(request, courriel, utilisateur, True)
        return utilisateur
