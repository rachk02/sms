"""Point d'entrée des rappels de Jasmin : accusés et SMS entrants.

Jasmin relaie par la même adresse ses accusés (champs `message_status`,
`level`) et les SMS reçus (champs `from`, `to`, `content`). Ce point d'entrée
vérifie le jeton, trie, dépose chacun dans sa file par la boîte d'envoi, et
répond aussitôt : rien n'est traité ici. Le worker accusés fait le reste.

Il n'est joignable que depuis le réseau interne et ne lit aucune table :
l'identifiant du connecteur est dans l'adresse, et le rôle applicatif ne peut
pas lire la table des connecteurs.
"""

import logging
import uuid

from django.http import HttpResponse, HttpResponseBadRequest, HttpResponseForbidden
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from apps.core.evenements import publier
from apps.core.files import TOPOLOGIE
from apps.telecom import accuses
from apps.telecom.jasmin import ACQUITTEMENT_RAPPEL, Jasmin
from apps.telecom.models import Connecteur
from apps.telecom.rappels import jeton_valide

journal = logging.getLogger(__name__)

FILE_ENTRANTS = "messages.entrants"


def _trier(donnees: dict) -> str | None:
    if "message_status" in donnees:
        return "accuse"
    if {"from", "to", "content"} <= donnees.keys():
        return "entrant"
    return None


@csrf_exempt
@require_POST
def rappel_jasmin(request, connecteur_id: uuid.UUID):
    if not jeton_valide(connecteur_id, request.GET.get("jeton", "")):
        journal.warning("Rappel refusé pour %s : jeton absent ou faux.", connecteur_id)
        return HttpResponseForbidden()
    donnees = request.POST.dict()
    nature = _trier(donnees)

    if nature == "accuse":
        passerelle = Jasmin(Connecteur(id=connecteur_id, nom=f"jasmin:{connecteur_id}"))
        accuse = passerelle.interpreter_rappel(donnees)
        if accuse is not None:
            publier(
                TOPOLOGIE.nom(accuses.FILE),
                echange=TOPOLOGIE.echange,
                donnees=accuses.vers_dict(accuse),
                cle_idempotence=f"accuse:{connecteur_id}:{accuse.reference_smsc}:{accuse.etat}",
            )
    elif nature == "entrant":
        reference = donnees.get("id") or str(uuid.uuid4())
        publier(
            TOPOLOGIE.nom(FILE_ENTRANTS),
            echange=TOPOLOGIE.echange,
            donnees={
                "connecteur_id": str(connecteur_id),
                "reference": reference,
                "numero_expediteur": donnees["from"],
                "numero_destination": donnees["to"],
                "texte": donnees["content"],
                "recu_le": timezone.now().isoformat(),
            },
            cle_idempotence=f"entrant:{connecteur_id}:{reference}",
        )
    else:
        journal.warning("Rappel de nature inconnue pour %s : %s", connecteur_id, sorted(donnees))
        return HttpResponseBadRequest("Rappel ni accusé ni message entrant.")
    return HttpResponse(ACQUITTEMENT_RAPPEL, content_type="text/plain")
