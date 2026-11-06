"""Fournisseurs de paiement, derrière une abstraction.

Chaque fournisseur sait vérifier la signature de ses rappels, lire la
référence de transaction qu'ils annoncent, et dire d'où ils peuvent venir. Le
rappel n'est jamais la preuve d'un paiement, seulement le signal d'aller
vérifier : l'interrogation de l'API de statut vient avec T-85.

Aucun contrat marchand ni accès de bac à sable Orange Money ou Moov Money pour
l'instant (décision du 6 novembre 2026) : seul le fournisseur simulé existe.
Les adaptateurs réels s'écriront à la réception de la documentation marchande,
avec l'accord de l'utilisateur pour tout compte externe et tout secret.
"""

import hashlib
import hmac
import ipaddress
import json
import time
from dataclasses import dataclass

from django.conf import settings

from apps.core.redis import client_redis


@dataclass(frozen=True)
class Rappel:
    """Ce qu'un rappel annonce : rien de plus qu'une référence à vérifier."""

    reference: str


@dataclass(frozen=True)
class Statut:
    """Ce que l'API de statut du fournisseur dit d'une référence. Seul un statut
    CONFIRME, au montant attendu, crédite. INCONNU : référence qu'il ignore."""

    etat: str  # CONFIRME, ECHOUE, EN_ATTENTE ou INCONNU
    montant: int | None = None


class Fournisseur:
    code: str = ""
    nom_dans_l_adresse: str = ""

    def statut(self, reference: str) -> Statut:
        """Interroge l'API de statut. Appel réseau : jamais sous un verrou."""
        raise NotImplementedError

    def plages(self) -> list:
        """Plages d'adresses d'où ce fournisseur rappelle, lues dans les réglages.
        Vide : aucun rappel n'est accepté."""
        brutes = settings.PAIEMENTS_PLAGES.get(self.nom_dans_l_adresse, [])
        return [ipaddress.ip_network(plage, strict=False) for plage in brutes]

    def signature_valide(self, entetes, corps: bytes) -> bool:
        raise NotImplementedError

    def lire_rappel(self, corps: bytes) -> Rappel | None:
        raise NotImplementedError


ENTETE_SIGNATURE = "X-Signature"
FENETRE_REJEU_S = 300


def signer(secret: str, corps: bytes, horodatage: int | None = None) -> str:
    """En-tête de signature : horodatage et condensat HMAC-SHA256 de
    « horodatage.corps », le schéma que la plateforme applique à ses propres
    webhooks."""
    horodatage = int(time.time()) if horodatage is None else horodatage
    condensat = hmac.new(
        secret.encode(), f"{horodatage}.".encode() + corps, hashlib.sha256
    ).hexdigest()
    return f"t={horodatage},v1={condensat}"


class Simule(Fournisseur):
    """Le fournisseur simulé du poste de développement et des tests. Il signe ses
    rappels comme le fera un vrai fournisseur, avec un secret partagé et un
    horodatage qui ferme la porte au rejeu au-delà de cinq minutes."""

    code = "SIMULE"
    nom_dans_l_adresse = "simule"

    def signature_valide(self, entetes, corps: bytes) -> bool:
        secret = settings.PAIEMENTS_SIMULE_SECRET
        brute = entetes.get(ENTETE_SIGNATURE, "")
        if not secret or not brute:
            return False
        try:
            champs = dict(morceau.split("=", 1) for morceau in brute.split(","))
            horodatage = int(champs["t"])
            propose = champs["v1"]
        except KeyError, ValueError:
            return False
        if abs(time.time() - horodatage) > FENETRE_REJEU_S:
            return False
        attendu = signer(secret, corps, horodatage).split("v1=", 1)[1]
        return hmac.compare_digest(attendu, propose)

    def statut(self, reference: str) -> Statut:
        etat = client_redis().hgetall(CLE_SIMULATION.format(reference=reference))
        if not etat:
            return Statut("INCONNU")
        return Statut(etat[b"etat"].decode(), int(etat[b"montant"]))

    def lire_rappel(self, corps: bytes) -> Rappel | None:
        try:
            reference = json.loads(corps)["reference"]
        except ValueError, KeyError, TypeError:
            return None
        if not isinstance(reference, str) or not 0 < len(reference) <= 100:
            return None
        return Rappel(reference=reference)


# Le côté du fournisseur simulé : ce que son API de statut répondra. Redis
# suffit, c'est un système extérieur imité, pas une donnée de la plateforme.
CLE_SIMULATION = "sms:paiements:simule:{reference}"


def simuler(reference: str, *, montant: int, etat: str) -> None:
    """Fixe ce que le fournisseur simulé sait d'un paiement : le client a payé
    (CONFIRME), refusé (ECHOUE) ou n'a encore rien fait (EN_ATTENTE)."""
    cle = CLE_SIMULATION.format(reference=reference)
    client_redis().hset(cle, mapping={"etat": etat, "montant": montant})
    client_redis().expire(cle, 30 * 24 * 3600)


def par_code(code: str) -> Fournisseur | None:
    return next((f for f in actifs().values() if f.code == code), None)


def actifs() -> dict[str, Fournisseur]:
    """Les fournisseurs joignables, par leur nom dans l'adresse de rappel. Le
    simulé n'existe que si les réglages l'activent, jamais en production."""
    fournisseurs = {}
    if settings.PAIEMENTS_SIMULATEUR:
        fournisseurs[Simule.nom_dans_l_adresse] = Simule()
    return fournisseurs
