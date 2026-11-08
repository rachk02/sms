"""Authentification et forme des réponses de l'API publique (T-106).

Chaque route publique passe par `route_api`, qui :

- ignore la session du navigateur : seule la clé présentée dans l'en-tête
  `Authorization: Bearer sk_...` authentifie, et le client posé est toujours
  celui de la clé, jamais celui d'un cookie (les routes sont exemptées du jeton
  anti-falsification, une session ne doit donc rien y ouvrir) ;
- vérifie la clé à chaque appel, sans cache (`comptes.services.verifier_cle`) ;
- pose l'en-tête `Api-Version` sur toute réponse (`docs/api/politique-de-version.md`).

Les erreurs ont une seule forme :
`{"erreur": {"code", "message", "requete_id", "champs"?}}`.
"""

from functools import wraps

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from apps.comptes.services import verifier_cle
from apps.core import journalisation
from apps.core.cloisonnement import poser_client

from . import quotas

VERSION = "1"
ENTETE_VERSION = "Api-Version"


def requete_id() -> str:
    return journalisation.lire("requete_id") or ""


def reponse(donnees, statut: int = 200, entetes: dict | None = None) -> JsonResponse:
    sortie = JsonResponse(donnees, status=statut, json_dumps_params={"ensure_ascii": False})
    sortie[ENTETE_VERSION] = VERSION
    sortie["Cache-Control"] = "no-store"
    for nom, valeur in (entetes or {}).items():
        sortie[nom] = valeur
    return sortie


def erreur(
    statut: int, code: str, message: str, *, champs=None, entetes: dict | None = None
) -> JsonResponse:
    corps = {"code": code, "message": message, "requete_id": requete_id()}
    if champs:
        corps["champs"] = champs
    return reponse({"erreur": corps}, statut, entetes)


def _jeton(request) -> str:
    entete = request.headers.get("Authorization", "")
    schema, _, valeur = entete.partition(" ")
    return valeur.strip() if schema.lower() == "bearer" else ""


def route_api(*methodes: str):
    """Décore une vue de l'API publique. `request.cle_api` porte la clé
    vérifiée ; `request.user` n'est jamais lu par une vue de l'API."""

    def decorateur(vue):
        @csrf_exempt
        @wraps(vue)
        def enveloppe(request, *args, **kwargs):
            # Le client posé par le cloisonnement vient de la session : il est
            # retiré avant tout, l'API ne connaît que celui de la clé.
            poser_client(None)
            if request.method not in methodes:
                return erreur(
                    405,
                    "METHODE_REFUSEE",
                    f"Méthode {request.method} non acceptée sur cette route.",
                    entetes={"Allow": ", ".join(methodes)},
                )
            jeton = _jeton(request)
            cle = verifier_cle(jeton, version=VERSION) if jeton else None
            if cle is None:
                return erreur(
                    401,
                    "CLE_INVALIDE",
                    "Clé d'API absente, inconnue ou révoquée.",
                    entetes={"WWW-Authenticate": "Bearer"},
                )
            request.cle_api = cle
            # Quota par clé (T-107) : un jeton par appel, en rafale et soutenu.
            decision = quotas.prendre(cle.id)
            if not decision.accepte:
                return erreur(
                    429,
                    "QUOTA_CLE_DEPASSE",
                    "Trop d'appels avec cette clé ; réessayez après le délai indiqué.",
                    entetes={"Retry-After": str(decision.attente_s), **decision.entetes},
                )
            sortie = vue(request, *args, **kwargs)
            sortie[ENTETE_VERSION] = VERSION
            # Le restant des deux niveaux, lu après la vue : un envoi vient
            # peut-être de consommer le plafond du compte.
            for nom, valeur in {**decision.entetes, **quotas.entetes_compte(cle.client)}.items():
                sortie[nom] = valeur
            return sortie

        # Lu par la documentation (T-110) : les méthodes documentées sont celles-ci.
        enveloppe.methodes = tuple(methodes)
        return enveloppe

    return decorateur
