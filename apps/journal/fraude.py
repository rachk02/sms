"""Détection de trafic artificiel sur le journal (T-100) : pics de codes vers
une plage inhabituelle, taux de livraison bas sur une plage.

Les fenêtres glissantes vivent dans Redis, comme celles de la santé des
liaisons ; les alertes et les seuils en base (`conformite.fraude`). Redis vidé,
les fenêtres repartent de zéro : une détection retardée, jamais une fausse
alerte.
"""

import re
import uuid
from datetime import timedelta

from apps.conformite import fraude
from apps.conformite.models import RegleFraude
from apps.core.etats import Etat
from apps.core.redis import client_redis

from .models import Message

# Un code à usage unique : de quatre à huit chiffres d'affilée.
CODE = re.compile(r"(?<!\d)\d{4,8}(?!\d)")
HISTORIQUE = timedelta(days=30)
CLE_CODES = "sms:fraude:codes:{client}:{plage}"
CLE_ISSUES = "sms:fraude:issues:{client}:{plage}"
LIVRES = (Etat.LIVRE,)
NON_LIVRES = (Etat.NON_LIVRE, Etat.EXPIRE)


def _fenetre(cle: str, membre: str, instant, minutes: int) -> list[bytes]:
    horodatage = instant.timestamp()
    redis = client_redis()
    with redis.pipeline() as lot:
        lot.zadd(cle, {membre: horodatage})
        lot.zremrangebyscore(cle, "-inf", horodatage - minutes * 60)
        lot.expire(cle, minutes * 60 * 2)
        lot.zrange(cle, 0, -1)
        return lot.execute()[-1]


def est_un_code(message) -> bool:
    return (
        message.campagne_id is None
        and message.nature == Message.Nature.CLIENT
        and bool(CODE.search(message.texte or ""))
    )


def noter_code(message) -> None:
    """À l'enregistrement d'un message hors campagne qui porte un code."""
    if not est_un_code(message):
        return
    regle = fraude.regle(RegleFraude.Code.CODES_PLAGE)
    if regle is None:
        return
    plage = fraude.plage(message.numero_normalise)
    cle = CLE_CODES.format(client=message.client_id, plage=plage)
    nombre = len(_fenetre(cle, str(message.id), message.date_reception, regle.fenetre_minutes))
    gravite = fraude.gravite(nombre, regle)
    if gravite is None:
        return
    debut_fenetre = message.date_reception - timedelta(minutes=regle.fenetre_minutes)
    habituelle = Message.objects.filter(
        client_id=message.client_id,
        numero_normalise__startswith=plage,
        date_reception__gte=message.date_reception - HISTORIQUE,
        date_reception__lt=debut_fenetre,
    ).exists()
    if habituelle:
        return
    fraude.signaler(
        message.client_id,
        RegleFraude.Code.CODES_PLAGE,
        gravite=gravite,
        valeur=nombre,
        plage=plage,
        details={"codes": nombre, "fenetre_minutes": regle.fenetre_minutes},
    )


def noter_issue(message, vers: str, instant) -> None:
    """À chaque accusé final d'un message d'un client."""
    if message.nature != Message.Nature.CLIENT or vers not in (*LIVRES, *NON_LIVRES):
        return
    regle = fraude.regle(RegleFraude.Code.LIVRAISON_PLAGE)
    if regle is None:
        return
    plage = fraude.plage(message.numero_normalise)
    cle = CLE_ISSUES.format(client=message.client_id, plage=plage)
    membre = f"{int(vers in LIVRES)}:{uuid.uuid4().hex}"
    issues = _fenetre(cle, membre, instant, regle.fenetre_minutes)
    total = len(issues)
    if total < max(1, regle.echantillon_min):
        return
    livres = sum(1 for issue in issues if issue.startswith(b"1:"))
    taux = livres * 100 // total
    gravite = fraude.gravite(taux, regle, bas=True)
    if gravite is None:
        return
    fraude.signaler(
        message.client_id,
        RegleFraude.Code.LIVRAISON_PLAGE,
        gravite=gravite,
        valeur=taux,
        plage=plage,
        details={"accuses": total, "livres": livres, "fenetre_minutes": regle.fenetre_minutes},
    )
