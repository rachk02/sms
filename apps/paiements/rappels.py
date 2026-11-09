"""Point d'entrée public des rappels des fournisseurs de paiement (T-84).

C'est le seul endroit de la plateforme où un tiers écrit vers nous sans
authentification préalable de notre côté. Trois contrôles, dans cet ordre :
la liste blanche des adresses du fournisseur, la limite de débit par adresse
source, puis la signature. Un rappel qui échoue à l'un d'eux, ou qui vise un
fournisseur inconnu, reçoit la même réponse nue, et la raison va au journal
applicatif : celui qui sonde n'apprend ni quels fournisseurs existent, ni
lequel des contrôles l'a arrêté.

Un rappel accepté est déposé dans la file `paiements.rappels` par la boîte
d'envoi, et la réponse part aussitôt. Ce point d'entrée ne lit aucune table :
la réponse est la même, au contenu comme au délai, qu'une transaction existe ou
non. Le worker vérifie ensuite auprès de l'API de statut du fournisseur, avant
tout crédit (T-85).
"""

import logging
import uuid

from django.conf import settings
from django.http import HttpResponse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt

from apps.core.evenements import publier
from apps.core.files import TOPOLOGIE
from apps.core.redis import client_redis
from apps.core.reseau import adresse_source, dans_plages

from . import fournisseurs

journal = logging.getLogger(__name__)

FILE = "paiements.rappels"
CLE_DEBIT = "sms:paiements:rappels:{source}:{minute}"


def _debit_depasse(source: str) -> bool:
    """Compteur Redis par adresse source et par minute."""
    cle = CLE_DEBIT.format(source=source, minute=int(timezone.now().timestamp() // 60))
    redis = client_redis()
    with redis.pipeline() as lot:
        lot.incr(cle)
        lot.expire(cle, 120, nx=True)
        compte = lot.execute()[0]
    return compte > settings.PAIEMENTS_RAPPELS_PAR_MINUTE


def _refus(request, nom: str, source: str):
    """La raison d'un refus, ou None. Le fournisseur, s'il existe, va avec."""
    fournisseur = fournisseurs.actifs().get(nom)
    if fournisseur is None:
        return "fournisseur inconnu", None
    if not dans_plages(source, fournisseur.plages()):
        return "adresse hors liste blanche", fournisseur
    if _debit_depasse(source):
        return "limite de débit dépassée", fournisseur
    if not fournisseur.signature_valide(request.headers, request.body):
        return "signature absente, fausse ou périmée", fournisseur
    return None, fournisseur


def _rejeter(nom: str, source: str, motif: str) -> HttpResponse:
    journal.warning(
        "Rappel de paiement rejeté (%s) depuis %s : %s.",
        nom[:40],
        source or "adresse inconnue",
        motif,
        extra={"source": source, "motif": motif},
    )
    return HttpResponse(status=403)


@csrf_exempt
def rappel_paiement(request, fournisseur: str):
    source = adresse_source(request)
    if request.method != "POST":
        return _rejeter(fournisseur, source, f"méthode {request.method}")
    motif, retenu = _refus(request, fournisseur, source)
    if motif:
        return _rejeter(fournisseur, source, motif)
    rappel = retenu.lire_rappel(request.body)
    if rappel is None:
        return _rejeter(fournisseur, source, "contenu illisible")
    # Une clé par réception, jamais par référence : le rappel réémis trois fois
    # arrive trois fois au worker, dont l'idempotence est ainsi vraiment éprouvée.
    publier(
        TOPOLOGIE.nom(FILE),
        echange=TOPOLOGIE.echange,
        donnees={
            "fournisseur": retenu.code,
            "reference": rappel.reference,
            "recu_le": timezone.now().isoformat(),
            "source": source,
        },
        cle_idempotence=f"rappel-paiement:{uuid.uuid4()}",
    )
    return HttpResponse("OK", content_type="text/plain")
