"""Webhooks sortants (T-111) : points de réception, répartition, livraison signée.

Un événement destiné au client (`client.message.livre`, `client.message.echec`,
`client.message.entrant`, `client.solde.bas`) est publié par le module qui le
constate, dans sa transaction, par la boîte d'envoi. La file
`webhooks.evenements` le reçoit ; `repartir` en tire une livraison par point
de réception actif abonné à cet événement, et dépose chacune dans
`webhooks.sortants`. `livrer` l'envoie en POST, signé, sans suivre de
redirection. Un échec renvoie l'ordre au palier de réessai suivant (une minute,
cinq, trente, deux heures, six heures), puis au rebut.

La signature suit le modèle courant : l'en-tête `Signature` porte
`t=<horodatage unix>,v1=<HMAC-SHA256 hexadécimal de "<t>.<corps>">`, avec le
secret du point. Le récepteur recalcule, compare en temps constant et refuse
un horodatage de plus de cinq minutes : un corps intercepté ne se rejoue pas.
`verifier_signature` est l'implémentation de référence, reprise dans la
documentation.

Cinq échecs consécutifs sur un point, toutes livraisons confondues, le
désactivent et préviennent son propriétaire par courriel. Les livraisons encore
en attente vers un point désactivé sont abandonnées sans tentative.
"""

import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from urllib.parse import urlsplit

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import Livraison, PointReception

journal = logging.getLogger(__name__)

FILE_SORTANTS = "webhooks.sortants"
ENTETE_SIGNATURE = "Signature"
TOLERANCE_S = 300
DELAI_S = 10
ECHECS_AVANT_DESACTIVATION = 5
POINTS_MAXIMUM = 10
TAILLE_ERREUR = 200


class PointRefuse(ValueError):
    pass


class LivraisonEchouee(Exception):
    """Le point a répondu en erreur ou n'a pas répondu : l'ordre repart au
    palier suivant."""


# Adresse


def _adresses_privees_autorisees() -> bool:
    return bool(getattr(settings, "WEBHOOKS_ADRESSES_PRIVEES", False))


def _hote_public(hote: str) -> bool:
    """Vrai si toutes les adresses de cet hôte sont publiques. Une adresse
    interne (boucle locale, réseau privé, lien local, métadonnées d'un nuage)
    ferait de la plateforme un relais vers son propre réseau."""
    try:
        adresses = {info[4][0] for info in socket.getaddrinfo(hote, None)}
    except OSError:
        return False
    for adresse in adresses:
        ip = ipaddress.ip_address(adresse.split("%")[0])
        if not ip.is_global:
            return False
    return bool(adresses)


def verifier_adresse(url: str) -> str:
    url = (url or "").strip()
    morceaux = urlsplit(url)
    privees = _adresses_privees_autorisees()
    schemas = ("https", "http") if privees else ("https",)
    if morceaux.scheme not in schemas or not morceaux.hostname:
        raise PointRefuse("L'adresse doit commencer par https:// et nommer un hôte.")
    if morceaux.username or morceaux.password:
        raise PointRefuse("L'adresse ne porte pas d'identifiants.")
    if len(url) > 500:
        raise PointRefuse("L'adresse dépasse 500 caractères.")
    if not privees and not _hote_public(morceaux.hostname):
        raise PointRefuse("L'hôte doit être joignable publiquement.")
    return url


# Points de réception


def _verifier_evenements(evenements) -> list[str]:
    retenus = sorted(set(evenements or []))
    if not retenus:
        raise PointRefuse("Choisissez au moins un événement.")
    inconnus = set(retenus) - set(PointReception.Evenement.values)
    if inconnus:
        raise PointRefuse("Événement inconnu : " + ", ".join(sorted(inconnus)))
    return retenus


@transaction.atomic
def creer_point(*, client, url: str, evenements, auteur, adresse_ip=None):
    """Déclare un point et rend son secret de signature, montré cette seule
    fois ; il est gardé chiffré par la clé applicative."""
    from apps.core.audit import auditer
    from apps.core.chiffrement import chiffrer

    url = verifier_adresse(url)
    evenements = _verifier_evenements(evenements)
    if PointReception.objects.filter(client=client, actif=True).count() >= POINTS_MAXIMUM:
        raise PointRefuse(f"Un compte déclare au plus {POINTS_MAXIMUM} points actifs.")
    secret = f"whsec_{secrets.token_urlsafe(32)}"
    point = PointReception.objects.create(
        client=client,
        url=url,
        secret_chiffre=chiffrer(secret),
        evenements=evenements,
        cree_par=auteur,
    )
    auditer(
        "api.webhook.creation",
        point,
        auteur=auteur,
        client=client,
        apres={"url": url, "evenements": evenements},
        adresse_ip=adresse_ip,
    )
    return point, secret


def _verrouiller(point) -> PointReception:
    return PointReception.objects.select_for_update().get(id=point.id)


@transaction.atomic
def desactiver_point(point, *, auteur, adresse_ip=None, motif="") -> PointReception:
    from apps.core.audit import auditer

    point = _verrouiller(point)
    if not point.actif:
        return point
    point.actif = False
    point.desactive_le = timezone.now()
    point.save(update_fields=["actif", "desactive_le"])
    auditer(
        "api.webhook.desactivation",
        point,
        auteur=auteur,
        client=point.client,
        apres={"motif": motif or "demande du client"},
        adresse_ip=adresse_ip,
    )
    return point


@transaction.atomic
def reactiver_point(point, *, auteur, adresse_ip=None) -> PointReception:
    """Remet en service un point désactivé, compteur d'échecs à zéro. Les
    livraisons abandonnées entretemps ne repartent pas."""
    from apps.core.audit import auditer

    point = _verrouiller(point)
    if point.actif:
        return point
    verifier_adresse(point.url)
    point.actif = True
    point.echecs_consecutifs = 0
    point.desactive_le = None
    point.save(update_fields=["actif", "echecs_consecutifs", "desactive_le"])
    auditer(
        "api.webhook.reactivation", point, auteur=auteur, client=point.client, adresse_ip=adresse_ip
    )
    return point


# Répartition


def repartir(evenement: dict) -> list[Livraison]:
    """Tire d'un événement du bus une livraison par point actif abonné, et
    dépose chacune dans `webhooks.sortants`. Appelé une fois par événement
    (`traiter_une_fois`) ; une livraison existe au plus une fois par point."""
    from apps.core.cloisonnement import poser_client
    from apps.core.evenements import publier_lot
    from apps.core.files import TOPOLOGIE

    nom, client_id = evenement["nom"], evenement.get("client_id")
    if not client_id or nom not in PointReception.Evenement.values:
        return []
    poser_client(client_id)
    cle = evenement["cle_idempotence"]
    corps = {
        "id": cle,
        "type": nom,
        "cree_le": evenement.get("cree_le") or timezone.now().isoformat(),
        "donnees": evenement.get("donnees") or {},
    }
    livraisons = []
    for point in PointReception.objects.filter(
        client_id=client_id, actif=True, evenements__contains=[nom]
    ):
        livraison, creee = Livraison.objects.get_or_create(
            point=point,
            cle_evenement=cle,
            defaults={"client_id": client_id, "evenement": nom, "corps": corps},
        )
        if creee:
            livraisons.append(livraison)
    publier_lot(
        [
            {
                "nom": TOPOLOGIE.nom(FILE_SORTANTS),
                "echange": TOPOLOGIE.echange,
                "client_id": client_id,
                "objet_id": livraison.id,
                "donnees": {"livraison_id": str(livraison.id)},
                "cle_idempotence": f"livraison:{livraison.id}",
            }
            for livraison in livraisons
        ]
    )
    return livraisons


# Signature


def signer(secret: str, corps: bytes, horodatage: int) -> str:
    message = f"{horodatage}.".encode() + corps
    empreinte = hmac.new(secret.encode(), message, hashlib.sha256).hexdigest()
    return f"t={horodatage},v1={empreinte}"


def verifier_signature(
    secret: str, corps: bytes, entete: str, *, tolerance_s: int = TOLERANCE_S, maintenant=None
) -> bool:
    """Implémentation de référence côté récepteur : vrai si l'en-tête porte
    une signature v1 valide pour ce corps et un horodatage d'au plus cinq
    minutes, dans un sens comme dans l'autre."""
    try:
        paires = [morceau.strip().split("=", 1) for morceau in (entete or "").split(",")]
        if any(len(paire) != 2 for paire in paires):
            return False
        horodatage = int(next(valeur for cle, valeur in paires if cle == "t"))
    except StopIteration, ValueError:
        return False
    instant = int(time.time() if maintenant is None else maintenant)
    if abs(instant - horodatage) > tolerance_s:
        return False
    attendue = signer(secret, corps, horodatage).split("v1=", 1)[1]
    recues = [valeur for cle, valeur in paires if cle == "v1"]
    return any(hmac.compare_digest(attendue, recue) for recue in recues)


# Livraison


class _SansRedirection(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


_OUVREUR = urllib.request.build_opener(_SansRedirection)


@dataclass(frozen=True)
class Reponse:
    statut: int | None
    erreur: str = ""

    @property
    def reussie(self) -> bool:
        return self.statut is not None and 200 <= self.statut < 300


def _poster(url: str, corps: bytes, entetes: dict) -> Reponse:
    """Un POST, sans suivre de redirection : une redirection compte comme un
    échec. Le corps de la réponse n'est pas lu au-delà de quelques octets."""
    if not _adresses_privees_autorisees() and not _hote_public(urlsplit(url).hostname or ""):
        return Reponse(None, "hôte non public au moment de la livraison")
    requete = urllib.request.Request(url, data=corps, headers=entetes, method="POST")
    try:
        with _OUVREUR.open(requete, timeout=DELAI_S) as reponse:
            reponse.read(1024)
            return Reponse(reponse.status)
    except urllib.error.HTTPError as refus:
        return Reponse(refus.code, f"HTTP {refus.code}")
    except (urllib.error.URLError, OSError, ValueError) as panne:
        return Reponse(None, repr(panne)[:TAILLE_ERREUR])


def _prevenir_proprietaire(point) -> None:
    from apps.comptes.models import Utilisateur
    from apps.core.courriels import envoyer

    if point.cree_par_id and point.cree_par.is_active:
        destinataires = [point.cree_par.courriel]
    else:
        destinataires = list(
            Utilisateur.objects.filter(
                client_id=point.client_id, role=Utilisateur.Role.ADMIN, is_active=True
            ).values_list("courriel", flat=True)
        )
    texte = (
        f"Votre point de réception {point.url} a répondu en erreur "
        f"{ECHECS_AVANT_DESACTIVATION} fois de suite. Il est désactivé : plus aucun "
        "événement ne lui est envoyé. Vérifiez-le, puis réactivez-le depuis votre "
        "espace, rubrique Webhooks."
    )
    for destinataire in destinataires:
        envoyer(destinataire, "Point de réception désactivé", texte)


def _apres_echec(point) -> None:
    point.echecs_consecutifs += 1
    champs = ["echecs_consecutifs"]
    if point.actif and point.echecs_consecutifs >= ECHECS_AVANT_DESACTIVATION:
        point.actif = False
        point.desactive_le = timezone.now()
        champs += ["actif", "desactive_le"]
        journal.warning("Point de réception %s désactivé après cinq échecs.", point.id)
        _prevenir_proprietaire(point)
    point.save(update_fields=champs)


def livrer(livraison_id) -> Livraison:
    """Une tentative de livraison. Lève `LivraisonEchouee` si le point répond
    en erreur ou ne répond pas : le worker renvoie l'ordre au palier suivant.
    Sans effet sur une livraison déjà livrée ou abandonnée."""
    from apps.core.chiffrement import dechiffrer

    livraison = Livraison.objects.select_related("point").get(id=livraison_id)
    if livraison.etat != Livraison.Etat.EN_COURS:
        return livraison
    point = livraison.point
    if not point.actif:
        return abandonner(livraison_id, "point de réception désactivé")
    corps = json.dumps(livraison.corps, ensure_ascii=False, separators=(",", ":")).encode()
    horodatage = int(time.time())
    reponse = _poster(
        point.url,
        corps,
        {
            "Content-Type": "application/json",
            "User-Agent": "EL-IMMEKA-Webhooks/1",
            ENTETE_SIGNATURE: signer(dechiffrer(point.secret_chiffre), corps, horodatage),
            "Webhook-Id": str(livraison.cle_evenement),
            "Webhook-Evenement": livraison.evenement,
        },
    )
    with transaction.atomic():
        livraison = Livraison.objects.select_for_update(of=("self",)).get(id=livraison_id)
        point = _verrouiller(livraison.point)
        livraison.tentatives += 1
        livraison.dernier_statut_http = reponse.statut
        livraison.derniere_erreur = reponse.erreur[:TAILLE_ERREUR]
        livraison.derniere_tentative_le = timezone.now()
        champs = ["tentatives", "dernier_statut_http", "derniere_erreur", "derniere_tentative_le"]
        if reponse.reussie:
            livraison.etat = Livraison.Etat.LIVREE
            livraison.livree_le = livraison.derniere_tentative_le
            champs += ["etat", "livree_le"]
            if point.echecs_consecutifs:
                point.echecs_consecutifs = 0
                point.save(update_fields=["echecs_consecutifs"])
        else:
            _apres_echec(point)
        livraison.save(update_fields=champs)
    if not reponse.reussie:
        raise LivraisonEchouee(reponse.erreur or f"HTTP {reponse.statut}")
    return livraison


@transaction.atomic
def abandonner(livraison_id, motif: str) -> Livraison:
    """Met une livraison au rebut : tous les paliers épuisés, ou point
    désactivé. Elle reste au journal, consultable par le client."""
    livraison = Livraison.objects.select_for_update().get(id=livraison_id)
    if livraison.etat == Livraison.Etat.EN_COURS:
        livraison.etat = Livraison.Etat.REBUT
        livraison.derniere_erreur = motif[:TAILLE_ERREUR]
        livraison.save(update_fields=["etat", "derniere_erreur"])
    return livraison
