"""Lectures du journal des messages. Aucune écriture."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta

from django.db import connection
from django.db.models import Q, Sum
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from apps.core.etats import Etat
from apps.core.numeros import NumeroInvalide, normaliser
from apps.core.tableur import ligne as ligne_csv
from apps.telecom import correlation
from apps.telecom.supervision import etat_publie

from .models import HORS_CAMPAGNE, Compteur, Evenement, Message

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
    # Le texte d'un message peut venir d'un tiers : aucune cellule ne sort en
    # formule exécutable par un tableur.
    return ligne_csv(valeurs)


def _date(valeur) -> str:
    return timezone.localtime(valeur).isoformat(timespec="seconds") if valeur else ""


def export_csv(client_id, debut: datetime, fin: datetime, filtres: Filtres | None = None):
    """Les messages du client sur [debut, fin), en CSV ligne par ligne, par lots
    paginés par curseur sur (date_reception, id) : jamais d'OFFSET. Les filtres
    de la recherche (T-93) s'appliquent aussi à l'export."""
    yield "\ufeff" + _ligne(COLONNES_EXPORT)
    curseur = None
    while True:
        lot = Message.objects.filter(
            client_id=client_id, date_reception__gte=debut, date_reception__lt=fin
        )
        if filtres:
            lot = filtres.appliquer(lot)
        if curseur:
            # Borne simple d'abord, pour que l'index démarre au curseur.
            lot = lot.filter(date_reception__gte=curseur[0]).filter(
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


# Recherche dans le journal des messages (T-93)

# Une recherche à l'écran porte sur 31 jours au plus ; au-delà, l'écran propose
# l'export, qui va jusqu'à un an et lit par lots.
RECHERCHE_JOURS_MAX = 31
RECHERCHE_JOURS_PAR_DEFAUT = 7
TAILLE_PAGE = 50


class RechercheRefusee(ValueError):
    pass


@dataclass(frozen=True)
class Filtres:
    debut: datetime
    fin: datetime
    # Dates telles que saisies, pour réafficher le formulaire et bâtir les liens.
    jour_debut: str
    jour_fin: str
    etat: str = ""
    campagne_id: uuid.UUID | None = None
    numero: str = ""

    @property
    def jours(self) -> int:
        return (self.fin - self.debut).days

    @property
    def au_dela_du_seuil(self) -> bool:
        return self.jours > RECHERCHE_JOURS_MAX

    def appliquer(self, messages):
        if self.etat:
            messages = messages.filter(etat=self.etat)
        if self.campagne_id:
            messages = messages.filter(campagne_id=self.campagne_id)
        if self.numero:
            # Égalité sur la colonne normalisée : l'index (client, numéro, date)
            # sert, jamais de LIKE.
            messages = messages.filter(numero_normalise=self.numero)
        return messages

    def parametres(self) -> dict:
        """Les filtres en paramètres d'adresse, pour l'export et la page suivante."""
        valeurs = {
            "debut": self.jour_debut,
            "fin": self.jour_fin,
            "etat": self.etat,
            "campagne": str(self.campagne_id or ""),
            "numero": self.numero,
        }
        return {cle: valeur for cle, valeur in valeurs.items() if valeur}


def lire_filtres(params, maintenant: datetime | None = None) -> Filtres:
    """Les filtres d'une recherche, validés. Sans aucune date, la semaine qui
    finit aujourd'hui ; une seule des deux, ou une plage à l'envers, est refusée :
    jamais de recherche sans borne de dates."""
    debut, fin = params.get("debut", ""), params.get("fin", "")
    if not debut and not fin:
        aujourd_hui = timezone.localdate(maintenant)
        debut = str(aujourd_hui - timedelta(days=RECHERCHE_JOURS_PAR_DEFAUT - 1))
        fin = str(aujourd_hui)
    try:
        depart, arrivee = periode_d_export(debut, fin)
    except ValueError as refus:
        raise RechercheRefusee(str(refus)) from None
    etat = params.get("etat", "")
    if etat and etat not in Etat.values:
        raise RechercheRefusee("État inconnu.")
    campagne = params.get("campagne", "").strip()
    try:
        campagne_id = uuid.UUID(campagne) if campagne else None
    except ValueError:
        raise RechercheRefusee("Identifiant de campagne invalide.") from None
    numero = params.get("numero", "").strip()
    if numero:
        try:
            numero = normaliser(numero)
        except NumeroInvalide:
            raise RechercheRefusee(f"Numéro invalide : {numero}.") from None
    return Filtres(depart, arrivee, debut, fin, etat, campagne_id, numero)


@dataclass(frozen=True)
class Page:
    messages: list
    # Curseur de la page suivante, None sur la dernière.
    suivant: str | None


def _curseur(message) -> str:
    return f"{message.date_reception.isoformat()}_{message.id}"


def _lire_curseur(valeur: str):
    try:
        date, identifiant = valeur.rsplit("_", 1)
        moment = parse_datetime(date)
        if moment is None:
            raise ValueError
        return moment, uuid.UUID(identifiant)
    except ValueError:
        raise RechercheRefusee("Page introuvable : reprenez la recherche au début.") from None


def requete_de_recherche(client_id, filtres: Filtres, apres: str | None = None):
    """La requête d'une page, avant sa limite : bornée sur la date de réception,
    clé de partition, et ordonnée pour l'index (client, date)."""
    if filtres.au_dela_du_seuil:
        raise RechercheRefusee(
            f"Une recherche à l'écran couvre {RECHERCHE_JOURS_MAX} jours au plus : "
            "exportez la période."
        )
    messages = filtres.appliquer(
        Message.objects.filter(
            client_id=client_id,
            date_reception__gte=filtres.debut,
            date_reception__lt=filtres.fin,
        )
    )
    if apres:
        moment, identifiant = _lire_curseur(apres)
        # La borne simple, redondante avec le OU, est celle que l'index sait
        # utiliser : le parcours commence au curseur au lieu de relire et
        # d'écarter toutes les pages précédentes.
        messages = messages.filter(date_reception__lte=moment).filter(
            Q(date_reception__lt=moment) | Q(date_reception=moment, id__lt=identifiant)
        )
    return messages.order_by("-date_reception", "-id").only(
        "id",
        "date_reception",
        "numero_normalise",
        "operateur",
        "expediteur_libelle",
        "etat",
        "cout_total",
        "motif_echec",
        "campagne_id",
        "nature",
    )


def rechercher(client_id, filtres: Filtres, apres: str | None = None, taille: int = TAILLE_PAGE):
    """Une page de messages, des plus récents aux plus anciens, paginée par
    curseur sur (date de réception, id) : la page cent coûte la page une."""
    lot = list(requete_de_recherche(client_id, filtres, apres)[: taille + 1])
    suivant = _curseur(lot[taille - 1]) if len(lot) > taille else None
    return Page(lot[:taille], suivant)


# Fiche d'un message (T-94)

# Ce que veut dire un motif posé avant le réseau. Les codes viennent des
# contrôles des modules (liste noire, expéditeurs, comptes) et du journal ; ils
# sont repris ici en clair, sans importer ces modules.
EXPLICATIONS = {
    "LISTE_NOIRE": (
        "Le numéro est sur la liste noire du compte ou de la plateforme : la personne "
        "s'est désinscrite, ou le numéro a été bloqué. Le message n'est pas parti."
    ),
    "EXPEDITEUR_NON_APPROUVE": (
        "L'expéditeur n'était pas approuvé pour ce réseau au moment de l'envoi. "
        "Le message n'est pas parti."
    ),
    "BAC_A_SABLE_NUMERO_NON_VERIFIE": (
        "Compte en bac à sable : seuls vos numéros de test vérifiés reçoivent les envois. "
        "Le message n'est pas parti."
    ),
    "ANNULE_PAR_LE_CLIENT": "Annulé avec sa campagne avant de partir.",
    "RETENU_PAR_LE_CLIENT": "Retenu : sa campagne est en pause.",
    "SOUMISSION_INTERROMPUE_SORT_INCONNU": (
        "La soumission a été interrompue et la passerelle n'a pas pu dire si le message "
        "était parti. Il n'est pas renvoyé, pour ne pas risquer un doublon : "
        "l'exploitation le vérifie."
    ),
}
LIBELLES_DETAILS = {
    "libelle_operateur": "Réponse de l'opérateur",
    "motif": "Motif",
    "code": "Code",
    "reference_smsc": "Référence chez l'opérateur",
    "reprise": "Reprise",
    "validite_heures": "Validité (heures)",
    "connecteur": "Liaison",
}
# Les événements d'un message suivent sa réception ; la marge couvre un accusé
# daté par l'horloge de l'opérateur. Borne haute : la plus longue attente d'un
# message avant soumission, plus la validité.
AVANT_RECEPTION = timedelta(days=1)
APRES_RECEPTION = timedelta(days=45)


@dataclass(frozen=True)
class Etape:
    horodatage: datetime
    etat: str
    libelle: str
    source: str
    details: list


@dataclass(frozen=True)
class Fiche:
    message: Message
    etapes: list
    liaison: str
    latence_s: float | None
    diagnostic: str

    def contexte_ticket(self) -> dict:
        """Ce qu'un ticket ouvert depuis la fiche emporte, sans saisie (T-102)."""
        m = self.message
        return {
            "message_id": str(m.id),
            "date_reception": m.date_reception.isoformat(),
            "numero": m.numero_normalise,
            "reseau": m.operateur,
            "expediteur": m.expediteur_libelle,
            "etat": m.etat,
            "motif_echec": m.motif_echec,
            "liaison": self.liaison,
            "reference_smsc": m.reference_smsc,
            "campagne_id": str(m.campagne_id or ""),
            "cout_total": m.cout_total,
            "date_soumission": m.date_soumission.isoformat() if m.date_soumission else "",
            "date_accuse": m.date_accuse.isoformat() if m.date_accuse else "",
            "diagnostic": self.diagnostic,
            "historique": [
                {"horodatage": e.horodatage.isoformat(), "etat": e.etat, "details": e.details}
                for e in self.etapes
            ],
        }


def _details(details: dict) -> list:
    return [(LIBELLES_DETAILS.get(cle, cle), str(valeur)) for cle, valeur in details.items()]


def _reponse_operateur(etapes: list) -> str:
    for etape in reversed(etapes):
        for libelle, valeur in etape.details:
            if libelle == LIBELLES_DETAILS["libelle_operateur"] and valeur:
                return f" Réponse de l'opérateur : {valeur}."
    return ""


def diagnostic(message: Message, etapes: list) -> str:
    """Pourquoi ce message en est là, en une ou deux phrases : la question la
    plus fréquente posée au support."""
    etat, reseau = message.etat, message.operateur or "le réseau"
    explication = EXPLICATIONS.get(message.motif_echec, "")
    reponse = _reponse_operateur(etapes)
    if etat in (Etat.RECU, Etat.EN_FILE):
        return f"En file : il part dès que la liaison vers {reseau} le permet."
    if etat == Etat.EN_ATTENTE:
        return explication or (
            "Retenu avant l'envoi : sa campagne est en pause, ou hors de la fenêtre "
            "légale des envois marketing. Il partira à la reprise."
        )
    if etat == Etat.SOUMIS:
        heures = int(correlation.validite().total_seconds() // 3600)
        return (
            f"Accepté par {reseau}, en attente de l'accusé de livraison. Sans accusé "
            f"dans les {heures} heures, il sera classé sans accusé."
        )
    if etat == Etat.LIVRE:
        return f"Livré au téléphone du destinataire, selon l'accusé de {reseau}."
    if etat == Etat.NON_LIVRE:
        return (
            f"{reseau} a renoncé à le livrer.{reponse} Causes fréquentes : téléphone éteint "
            "ou hors couverture pendant toute la durée de validité, numéro inactif ou "
            "inexistant."
        )
    if etat == Etat.EXPIRE:
        return f"Sa durée de validité a expiré avant que le téléphone soit joignable.{reponse}"
    if etat == Etat.SANS_ACCUSE:
        return (
            f"Accepté par {reseau}, mais l'opérateur n'a rendu aucun accusé dans le délai : "
            "on ne sait pas s'il a été livré."
        )
    if etat == Etat.REJETE:
        if message.date_soumission:
            return f"Refusé par {reseau} après l'avoir accepté.{reponse}"
        motif = f" : {message.motif_echec}" if message.motif_echec else ""
        return explication or f"Refusé avant de partir vers le réseau{motif}."
    if etat == Etat.ANNULE:
        return explication or "Annulé avant de partir."
    return ""


def fiche(client_id, message_id, date_reception: datetime) -> Fiche | None:
    """La fiche d'un message, lue par sa clé exacte (une seule partition), avec
    son historique d'états daté ; None s'il n'est pas à ce client."""
    message = Message.objects.filter(
        client_id=client_id, id=message_id, date_reception=date_reception
    ).first()
    if message is None:
        return None
    evenements = Evenement.objects.filter(
        client_id=client_id,
        message_id=message.id,
        horodatage__gte=date_reception - AVANT_RECEPTION,
        horodatage__lt=date_reception + APRES_RECEPTION,
    ).order_by("horodatage", "id")
    etapes = [
        Etape(
            e.horodatage,
            e.etat,
            Etat(e.etat).label,
            Evenement.Source(e.source).label,
            _details(e.details or {}),
        )
        for e in evenements
    ]
    liaison = ""
    if message.connecteur_id:
        liaisons, _ = etat_publie()
        publiee = liaisons.get(str(message.connecteur_id))
        liaison = publiee["nom"] if publiee else f"liaison {str(message.connecteur_id)[:8]}"
    latence = None
    if message.etat == Etat.LIVRE and message.date_soumission and message.date_accuse:
        latence = max(0.0, (message.date_accuse - message.date_soumission).total_seconds())
    return Fiche(message, etapes, liaison, latence, diagnostic(message, etapes))


# Lecture par identifiant seul, pour l'API (T-109)

# Un identifiant de message est un UUID version 7 : ses 48 premiers bits sont
# l'instant de sa création, à la milliseconde, pris au moment où le message est
# reçu. La recherche se borne à un jour de part et d'autre de cet instant :
# deux partitions au plus, jamais un balayage du journal.
MARGE_IDENTIFIANT = timedelta(days=1)


def instant_de(message_id: uuid.UUID) -> datetime | None:
    if message_id.version != 7:
        return None
    return datetime.fromtimestamp((message_id.int >> 80) / 1000, tz=UTC)


def fiche_par_identifiant(client_id, message_id: uuid.UUID) -> Fiche | None:
    """La fiche d'un message retrouvé par son seul identifiant ; None s'il
    n'existe pas, n'est pas à ce client ou n'est pas un UUID version 7."""
    instant = instant_de(message_id)
    if instant is None:
        return None
    date_reception = (
        Message.objects.filter(
            client_id=client_id,
            id=message_id,
            date_reception__gte=instant - MARGE_IDENTIFIANT,
            date_reception__lt=instant + MARGE_IDENTIFIANT,
        )
        .values_list("date_reception", flat=True)
        .first()
    )
    return fiche(client_id, message_id, date_reception) if date_reception else None
