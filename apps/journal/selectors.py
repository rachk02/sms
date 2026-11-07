"""Lectures du journal des messages. Aucune écriture."""

import csv
import io
from datetime import datetime, time, timedelta

from django.db import connection
from django.db.models import Q, Sum
from django.utils import timezone

from .models import HORS_CAMPAGNE, Compteur, Message

# Fenêtre du rapprochement d'un STOP avec ses émetteurs (T-70).
FENETRE_DESINSCRIPTION = timedelta(hours=72)

# Borne basse sur la date de réception, clé de partition : elle écarte les
# partitions anciennes. Un message soumis dans la fenêtre a été reçu au plus
# trente jours plus tôt (pause ou programmation comprises) : au-delà, la plage
# des recherches du journal s'arrête aussi.
RECULE_RECEPTION = timedelta(days=30)


def clients_touches(numero_normalise: str, jusqu_a: datetime) -> dict:
    """Clients qui ont soumis au moins un message vers ce numéro dans les
    soixante-douze heures qui précèdent `jusqu_a`, avec l'expéditeur de leur
    dernier message soumis : celui que la personne a lu.

    La date qui compte est celle de la soumission : un message d'une campagne
    reprise après une longue pause est parti récemment, même reçu longtemps
    avant. Un message jamais soumis (refusé avant le réseau, annulé, encore en
    attente) n'a touché personne. Un message système n'est pas un envoi du
    client."""
    messages = (
        Message.objects.filter(
            numero_normalise=numero_normalise,
            nature=Message.Nature.CLIENT,
            date_reception__gte=jusqu_a - FENETRE_DESINSCRIPTION - RECULE_RECEPTION,
            date_reception__lte=jusqu_a,
            date_soumission__gte=jusqu_a - FENETRE_DESINSCRIPTION,
            date_soumission__lte=jusqu_a,
        )
        .order_by("client_id", "-date_soumission")
        .distinct("client_id")
        .values_list("client_id", "expediteur_libelle")
    )
    return dict(messages)


def debut_du_mois(maintenant: datetime | None = None) -> datetime:
    """Premier instant du mois civil, à l'heure de Ouagadougou."""
    local = timezone.localtime(maintenant or timezone.now())
    return local.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def volume_mensuel(client_id, maintenant: datetime | None = None) -> int:
    """SMS soumis par le client depuis le début du mois civil, lus sur les
    compteurs pré-agrégés des campagnes, jamais sur `journal_message`. Sert au
    palier de volume des grilles tarifaires (T-82). Les envois hors campagne n'y
    sont pas comptés : ils ont leurs compteurs depuis T-92, mais les inclure
    changerait le prix, ce qui reste à trancher."""
    total = (
        Compteur.objects.filter(client_id=client_id, heure__gte=debut_du_mois(maintenant))
        .exclude(campagne_id=HORS_CAMPAGNE)
        .aggregate(total=Sum("emis"))["total"]
    )
    return total or 0


# Export du journal par le client (T-89)

EXPORT_JOURS_MAX = 366
EXPORT_LOT = 2_000
COLONNES_EXPORT = (
    "date_reception",
    "numero",
    "operateur",
    "expediteur",
    "nature",
    "texte",
    "segments",
    "cout_total",
    "etat",
    "motif_echec",
    "date_soumission",
    "date_accuse",
    "campagne_id",
)


def periode_d_export(debut: str | None, fin: str | None) -> tuple[datetime, datetime]:
    """[debut, fin) en heure locale, fin incluse dans la saisie : un an au plus."""
    from django.utils.dateparse import parse_date

    jour_debut, jour_fin = parse_date(debut or ""), parse_date(fin or "")
    if jour_debut is None or jour_fin is None:
        raise ValueError("Indiquez une date de début et une date de fin (AAAA-MM-JJ).")
    if jour_fin < jour_debut:
        raise ValueError("La date de fin précède la date de début.")
    if (jour_fin - jour_debut).days >= EXPORT_JOURS_MAX:
        raise ValueError(f"Un export couvre {EXPORT_JOURS_MAX} jours au plus.")
    depart = timezone.make_aware(datetime.combine(jour_debut, time.min))
    return depart, timezone.make_aware(datetime.combine(jour_fin + timedelta(days=1), time.min))


def _ligne(valeurs) -> str:
    tampon = io.StringIO()
    csv.writer(tampon, delimiter=";").writerow(valeurs)
    return tampon.getvalue()


def _date(valeur) -> str:
    return timezone.localtime(valeur).isoformat(timespec="seconds") if valeur else ""


def export_csv(client_id, debut: datetime, fin: datetime):
    """Les messages du client sur [debut, fin), en CSV ligne par ligne, par lots
    paginés par curseur sur (date_reception, id) : jamais d'OFFSET."""
    yield "﻿" + _ligne(COLONNES_EXPORT)
    curseur = None
    while True:
        lot = Message.objects.filter(
            client_id=client_id, date_reception__gte=debut, date_reception__lt=fin
        )
        if curseur:
            lot = lot.filter(
                Q(date_reception__gt=curseur[0]) | Q(date_reception=curseur[0], id__gt=curseur[1])
            )
        lot = list(
            lot.order_by("date_reception", "id").values_list(
                "date_reception",
                "numero_normalise",
                "operateur",
                "expediteur_libelle",
                "nature",
                "texte",
                "segments",
                "cout_total",
                "etat",
                "motif_echec",
                "date_soumission",
                "date_accuse",
                "campagne_id",
                "id",
            )[:EXPORT_LOT]
        )
        if not lot:
            return
        for recu, *milieu, soumis, accuse, campagne, _id in lot:
            yield _ligne([_date(recu), *milieu, _date(soumis), _date(accuse), campagne or ""])
        curseur = (lot[-1][0], lot[-1][-1])


# Contrôle des compteurs pré-agrégés (T-91)

# Ce que chaque colonne de `journal_compteur` compte, relu sur l'état final des
# messages. Un message ne passe qu'une fois par SOUMIS (qui pose la date de
# soumission) et ne finit que dans un seul état terminal : l'état final et la
# date de soumission suffisent à retrouver les transitions comptées.
CONTROLE = """
SELECT
    count(*) FILTER (WHERE date_soumission IS NOT NULL),
    count(*) FILTER (WHERE etat = 'LIVRE'),
    count(*) FILTER (
        WHERE etat IN ('NON_LIVRE', 'EXPIRE')
           OR (etat = 'REJETE' AND date_soumission IS NOT NULL)
    ),
    count(*) FILTER (WHERE etat = 'SANS_ACCUSE'),
    count(*) FILTER (WHERE etat = 'REJETE' AND date_soumission IS NULL),
    count(*) FILTER (WHERE etat = 'ANNULE'),
    coalesce(sum(cout_total) FILTER (WHERE date_soumission IS NOT NULL), 0),
    coalesce(sum(
        greatest(0, floor(extract(epoch FROM date_accuse - date_soumission) * 1000))
    ) FILTER (WHERE etat = 'LIVRE' AND date_soumission IS NOT NULL), 0),
    count(*) FILTER (WHERE etat = 'LIVRE' AND date_soumission IS NOT NULL)
FROM journal_message
WHERE client_id = %s AND campagne_id = %s AND date_reception >= %s AND date_reception < %s
"""
COLONNES_CONTROLE = (
    "emis",
    "livres",
    "echecs",
    "sans_accuse",
    "refuses",
    "annules",
    "cout",
    "latence_cumulee_ms",
    "latences",
)


def compte_journal(client_id, campagne_id, depuis: datetime, jusqu_a: datetime) -> dict:
    """Les chiffres d'une campagne recomptés dans le journal, bornés sur la date
    de réception : la requête de contrôle, réservée aux vérifications, jamais
    aux écrans."""
    with connection.cursor() as curseur:
        curseur.execute(CONTROLE, [client_id, campagne_id, depuis, jusqu_a])
        return dict(zip(COLONNES_CONTROLE, curseur.fetchone(), strict=True))


def compte_compteurs(client_id, campagne_id) -> dict:
    """Les mêmes chiffres, lus sur les compteurs pré-agrégés."""
    totaux = Compteur.objects.filter(client_id=client_id, campagne_id=campagne_id).aggregate(
        **{c: Sum(c) for c in COLONNES_CONTROLE}
    )
    return {c: totaux[c] or 0 for c in COLONNES_CONTROLE}
