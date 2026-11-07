"""Porte d'écriture du module telecom."""

from datetime import datetime

from django.db import transaction
from django.utils import timezone

from apps.core.evenements import publier

from .models import Connecteur, MessageEntrant, MotCle
from .mots_cles import reconnaitre

# Un STOP reçu est annoncé ; le journal, au-dessus, le rapproche des clients qui
# ont écrit à ce numéro (T-70).
DESINSCRIPTION_RECUE = "telecom.desinscription.recue"


def enregistrer_entrant(
    *,
    connecteur_id,
    reference: str,
    numero_expediteur: str,
    numero_destination: str,
    texte: str,
    recu_le: datetime,
) -> tuple[MessageEntrant, bool]:
    """Écrit un SMS entrant et le mot-clé qu'il porte, s'il en porte un, et
    annonce une désinscription dans la même transaction. Sans effet si la
    passerelle rejoue le même rappel : renvoie le message et vrai s'il vient
    d'être créé."""
    mot_cle = reconnaitre(texte)
    with transaction.atomic():
        entrant, cree = MessageEntrant.objects.get_or_create(
            connecteur_id=connecteur_id,
            reference=reference,
            defaults={
                "numero_expediteur": numero_expediteur,
                "numero_destination": numero_destination,
                "texte": texte,
                "mot_cle_reconnu": mot_cle.mot_cle if mot_cle else "",
                "recu_le": recu_le,
            },
        )
        if cree and mot_cle and mot_cle.action == MotCle.Action.DESINSCRIPTION:
            publier(
                DESINSCRIPTION_RECUE,
                objet_id=entrant.id,
                donnees={
                    "numero_expediteur": entrant.numero_expediteur,
                    "texte": entrant.texte,
                    "recu_le": entrant.recu_le.isoformat(),
                    "mot_cle": mot_cle.mot_cle,
                    # Le réseau d'où vient le STOP : celui qui porte la réponse.
                    "operateur": Connecteur.objects.get(id=connecteur_id).operateur,
                },
                cle_idempotence=f"desinscription:{entrant.id}",
            )
    return entrant, cree


def marquer_rapproche(entrant_id, clients: list) -> None:
    """Note qu'un SMS entrant a été rapproché. Le client n'est posé que s'il est
    seul : un STOP qui vaut pour plusieurs clients reste sans client, rapproché
    quand même, et chaque client le retrouve dans sa liste noire."""
    MessageEntrant.objects.filter(id=entrant_id, traite_le__isnull=True).update(
        client_id=clients[0] if len(clients) == 1 else None, traite_le=timezone.now()
    )


def connecteur_pour(operateur: str):
    """Connecteur qui porte les messages d'un opérateur. Avec des routes
    déclarées (T-97) : celui de la première route active, par priorité, dont le
    connecteur est actif et la liaison pas tombée ; aucun si toutes sont
    tombées. Sans route : le premier connecteur actif de l'opérateur dont la
    liaison n'est pas tombée. Lu à chaque message : un changement de débit ou
    de route prend effet sans redémarrer les workers. La bascule au taux de
    livraison est l'objet de la fiche F-38 (lot 10)."""
    from .models import Connecteur, Route

    routes = Route.objects.filter(operateur=operateur, actif=True).select_related("connecteur")
    routes = list(routes.order_by("priorite"))
    if routes:
        return next(
            (
                route.connecteur
                for route in routes
                if route.connecteur.actif
                and route.connecteur.etat_liaison != Connecteur.EtatLiaison.DECONNECTE
            ),
            None,
        )
    return (
        Connecteur.objects.filter(operateur=operateur, actif=True)
        .exclude(etat_liaison=Connecteur.EtatLiaison.DECONNECTE)
        .order_by("cree_le")
        .first()
    )


# Configuration des connecteurs et des routes (T-97)

# Champs recopiés au journal d'audit. Le secret n'y figure jamais, ni en clair
# ni chiffré : seulement s'il est présent, et s'il a été remplacé.
CHAMPS_CONNECTEUR = (
    "nom",
    "operateur",
    "type",
    "hote",
    "port",
    "identifiant",
    "debit_max",
    "sessions_max",
    "fenetre",
    "parametres",
    "actif",
)
# Les réglages de l'adaptateur ne portent jamais un secret : une clé qui en a
# l'air est refusée, le secret a son champ chiffré.
MOTS_DE_SECRET = ("secret", "password", "passwd", "mot_de_passe", "token", "jeton")


class ConfigurationRefusee(ValueError):
    pass


def verifier_parametres(parametres) -> None:
    if not isinstance(parametres, dict):
        raise ConfigurationRefusee("Les réglages de l'adaptateur sont un objet JSON.")
    for cle in parametres:
        if any(mot in str(cle).lower() for mot in MOTS_DE_SECRET):
            raise ConfigurationRefusee(
                f"Le réglage « {cle} » ressemble à un secret : il va dans le champ du secret, "
                "qui est chiffré."
            )


def _connecteur(connecteur) -> dict:
    instantane = {champ: getattr(connecteur, champ) for champ in CHAMPS_CONNECTEUR}
    instantane["secret"] = "présent" if connecteur.secret_chiffre else "absent"
    return instantane


@transaction.atomic
def enregistrer_connecteur(connecteur, *, agent, secret: str = "", adresse_ip=None):
    """Crée ou modifie un connecteur. `secret` vide garde le secret en place ;
    saisi, il est chiffré et jamais relu par l'écran. L'état de la liaison
    reste à la supervision. Trace au journal d'audit, avant et après."""
    from apps.core.audit import auditer
    from apps.core.chiffrement import chiffrer

    from .models import Connecteur

    verifier_parametres(connecteur.parametres or {})
    avant = None
    if not connecteur._state.adding:
        ancien = Connecteur.objects.select_for_update().get(id=connecteur.id)
        avant = _connecteur(ancien)
        connecteur.secret_chiffre = ancien.secret_chiffre
    if secret:
        connecteur.secret_chiffre = chiffrer(secret)
    if avant is None:
        connecteur.save()
    else:
        connecteur.save(update_fields=[*CHAMPS_CONNECTEUR, "secret_chiffre", "modifie_le"])
    apres = _connecteur(connecteur)
    if secret and avant is not None:
        apres["secret"] = "remplacé"
    auditer(
        "telecom.connecteur.creation" if avant is None else "telecom.connecteur.modification",
        connecteur,
        auteur=agent,
        avant=avant,
        apres=apres,
        adresse_ip=adresse_ip,
    )
    return connecteur


def _route(route) -> dict:
    return {
        "operateur": route.operateur,
        "connecteur": route.connecteur.nom,
        "priorite": route.priorite,
        "seuil_bascule": route.seuil_bascule,
        "actif": route.actif,
    }


@transaction.atomic
def enregistrer_route(route, *, agent, adresse_ip=None):
    from apps.core.audit import auditer

    from .models import Route

    avant = None
    if not route._state.adding:
        avant = _route(Route.objects.select_related("connecteur").get(id=route.id))
    route.save()
    auditer(
        "telecom.route.creation" if avant is None else "telecom.route.modification",
        route,
        auteur=agent,
        avant=avant,
        apres=_route(route),
        adresse_ip=adresse_ip,
    )
    return route


@transaction.atomic
def supprimer_route(route, *, agent, adresse_ip=None) -> None:
    from apps.core.audit import auditer

    avant = _route(route)
    auditer("telecom.route.suppression", route, auteur=agent, avant=avant, adresse_ip=adresse_ip)
    route.delete()
