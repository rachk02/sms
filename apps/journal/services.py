"""Porte d'écriture du module journal."""

import logging
from collections import Counter
from datetime import datetime, timedelta

from django.db import connection, connections, transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.core import controles
from apps.core.encodage import analyser
from apps.core.etats import ETATS_TERMINAUX, Etat, transition_permise
from apps.core.evenements import publier
from apps.core.numeros import NumeroInvalide, normaliser
from apps.core.redis import client_redis
from apps.telecom import correlation, sante
from apps.telecom.models import Connecteur, MotCle
from apps.telecom.numero_court import numero_court
from apps.telecom.passerelles import SortSoumission, Soumission
from apps.telecom.services import marquer_rapproche

from .models import HORS_CAMPAGNE, Compteur, Destinataire, Evenement, Message
from .selectors import clients_touches

journal = logging.getLogger(__name__)

TABLES_PARTITIONNEES = ("journal_message", "journal_evenement")

# Compteur de messages soumis par connecteur, lu par les métriques
# d'exploitation : jamais de comptage à la volée sur le journal.
CLE_SOUMIS = "sms:metriques:soumis:{connecteur_id}"


@transaction.atomic
def enregistrer_message(
    *,
    client,
    numero_normalise: str,
    operateur: str,
    expediteur_libelle: str,
    texte: str,
    cout_unitaire: int,
    campagne_id=None,
    contact_id=None,
    date_reception: datetime | None = None,
    source: str = Evenement.Source.API,
    **champs,
) -> Message:
    """Écrit un message au journal, avec son premier événement.

    Pour un message de campagne, la ligne de `journal_destinataire` est écrite
    dans la même transaction : si le contact a déjà été servi par cette
    campagne, l'unicité de cette table lève une IntegrityError et le message
    n'est pas écrit non plus.
    """
    analyse = analyser(texte)
    date_reception = date_reception or timezone.now()
    message = Message.objects.create(
        client=client,
        campagne_id=campagne_id,
        contact_id=contact_id,
        numero_normalise=numero_normalise,
        operateur=operateur,
        expediteur_libelle=expediteur_libelle,
        texte=texte,
        encodage=analyse.encodage,
        segments=analyse.segments,
        cout_unitaire=cout_unitaire,
        cout_total=cout_unitaire * analyse.segments,
        date_reception=date_reception,
        **champs,
    )
    if campagne_id and contact_id:
        Destinataire.objects.create(
            campagne_id=campagne_id,
            contact_id=contact_id,
            client=client,
            message_id=message.id,
            message_date_reception=date_reception,
        )
    Evenement.objects.create(
        client=client,
        message_id=message.id,
        etat=message.etat,
        horodatage=date_reception,
        source=source,
    )
    # Pic de codes vers une plage inhabituelle (T-100).
    from .fraude import noter_code

    noter_code(message)
    return message


def creer_partitions_a_venir(mois_d_avance: int = 2) -> list[str]:
    """Crée, si besoin, les partitions du mois courant et des mois suivants.

    Appelée par l'ordonnanceur. Passe par la connexion du propriétaire : seul
    lui peut créer une partition. Sans effet sur une partition déjà créée.
    """
    creees = []
    with connections["proprietaire"].cursor() as curseur:
        for table in TABLES_PARTITIONNEES:
            for decalage in range(mois_d_avance + 1):
                curseur.execute(
                    "SELECT journal_creer_partition(%s::regclass,"
                    " (date_trunc('month', now()) + make_interval(months => %s))::date)",
                    [table, decalage],
                )
                creees.append(curseur.fetchone()[0])
    return creees


# Compteurs du suivi (T-67) : la colonne qu'une transition incrémente.
COMPTER = """
INSERT INTO journal_compteur (
    client_id, campagne_id, operateur, expediteur_libelle, heure,
    emis, livres, echecs, sans_accuse, refuses, annules, cout, latence_cumulee_ms, latences
)
VALUES (
    %s, %s, %s, %s, date_trunc('hour', %s::timestamptz), %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (client_id, campagne_id, operateur, expediteur_libelle, heure) DO UPDATE SET
    emis = journal_compteur.emis + EXCLUDED.emis,
    livres = journal_compteur.livres + EXCLUDED.livres,
    echecs = journal_compteur.echecs + EXCLUDED.echecs,
    sans_accuse = journal_compteur.sans_accuse + EXCLUDED.sans_accuse,
    refuses = journal_compteur.refuses + EXCLUDED.refuses,
    annules = journal_compteur.annules + EXCLUDED.annules,
    cout = journal_compteur.cout + EXCLUDED.cout,
    latence_cumulee_ms = journal_compteur.latence_cumulee_ms + EXCLUDED.latence_cumulee_ms,
    latences = journal_compteur.latences + EXCLUDED.latences
"""
COLONNES = ("emis", "livres", "echecs", "sans_accuse", "refuses", "annules")


def _colonne(depuis: str, vers: str) -> str | None:
    """La colonne du compteur qu'incrémente une transition, ou None."""
    if vers == Etat.SOUMIS:
        return "emis"
    if vers == Etat.LIVRE:
        return "livres"
    if vers in (Etat.NON_LIVRE, Etat.EXPIRE):
        return "echecs"
    if vers == Etat.REJETE:
        # Refusé par l'opérateur après acceptation : un échec ; avant, un refus.
        return "echecs" if depuis == Etat.SOUMIS else "refuses"
    if vers == Etat.SANS_ACCUSE:
        return "sans_accuse"
    if vers == Etat.ANNULE:
        return "annules"
    return None


def _compter(
    *,
    client_id,
    campagne_id,
    operateur,
    expediteur,
    horodatage,
    colonne,
    nombre=1,
    cout=0,
    latence=None,
):
    valeurs = [nombre if c == colonne else 0 for c in COLONNES]
    latences = [latence, 1] if latence is not None else [0, 0]
    with connection.cursor() as curseur:
        curseur.execute(
            COMPTER,
            [client_id, campagne_id, operateur, expediteur, horodatage, *valeurs, cout, *latences],
        )


def latence_ms(soumis_le: datetime | None, livre_le: datetime) -> int | None:
    """Délai d'acheminement d'un message livré, en millisecondes entières,
    arrondi vers le bas ; zéro si l'opérateur date l'accusé avant la soumission."""
    if soumis_le is None:
        return None
    return max(0, (livre_le - soumis_le) // timedelta(milliseconds=1))


class TransitionInterdite(Exception):
    def __init__(self, message_id, depuis: str, vers: str):
        self.message_id, self.depuis, self.vers = message_id, depuis, vers
        super().__init__(f"Transition interdite pour {message_id} : {depuis} vers {vers}")


def changer_etat(
    message: Message,
    vers: str,
    *,
    source: str,
    horodatage: datetime | None = None,
    details: dict | None = None,
    **champs,
) -> None:
    """Seule porte de changement d'état d'un message. Vérifie la transition
    contre la table de `core.etats`, écrit le nouvel état et ses champs, puis
    un événement daté, et pour un message de campagne son compteur du suivi.
    Une transition interdite est journalisée et refusée.

    `message` doit porter l'état courant, lu sous verrou par l'appelant.
    """
    if not transition_permise(message.etat, vers):
        journal.warning(
            "Transition interdite refusée pour %s : %s vers %s.",
            message.id,
            message.etat,
            vers,
            extra={"message_id": str(message.id)},
        )
        raise TransitionInterdite(message.id, message.etat, vers)
    horodatage = horodatage or timezone.now()
    Message.objects.filter(id=message.id, date_reception=message.date_reception).update(
        etat=vers, modifie_le=timezone.now(), **champs
    )
    Evenement.objects.create(
        client_id=message.client_id,
        message_id=message.id,
        etat=vers,
        horodatage=horodatage,
        source=source,
        details=details or {},
    )
    colonne = _colonne(message.etat, vers)
    if colonne and (message.campagne_id or message.nature == Message.Nature.CLIENT):
        _compter(
            client_id=message.client_id,
            campagne_id=message.campagne_id or HORS_CAMPAGNE,
            operateur=message.operateur,
            expediteur=message.expediteur_libelle,
            horodatage=horodatage,
            colonne=colonne,
            cout=message.cout_total if colonne == "emis" else 0,
            latence=latence_ms(message.date_soumission, horodatage) if vers == Etat.LIVRE else None,
        )
    # Taux de livraison sur la plage du numéro (T-100).
    from .fraude import noter_issue

    noter_issue(message, vers, horodatage)
    message.etat = vers


def _verrouiller(message: Message) -> Message:
    return Message.objects.select_for_update().get(
        id=message.id, date_reception=message.date_reception
    )


MOTIF_SORT_INCONNU = "SOUMISSION_INTERROMPUE_SORT_INCONNU"


def soumettre(message: Message, passerelle, source: str = Evenement.Source.WORKER) -> Message:
    """Soumet un message à la passerelle et le passe SOUMIS, sans jamais le
    soumettre deux fois.

    Trois temps. D'abord le marqueur `soumission_debutee_le`, validé seul, avant
    tout appel. Puis l'appel à la passerelle, hors transaction. Enfin l'écriture
    du résultat. Un processus qui meurt entre les deux laisse le marqueur sans
    résultat : au rejeu, on demande alors à la passerelle le sort de cette
    soumission au lieu de la refaire. Si la passerelle ne sait pas le dire, le
    message n'est pas resoumis : il reste en file, motif posé, pour vérification.
    Un doublon atteint le destinataire et se facture ; un manque se rattrape.

    À appeler hors transaction, comme un worker le fait : sans cela, le marqueur
    ne serait pas validé avant l'appel.
    """
    if connection.in_atomic_block:
        raise RuntimeError("soumettre doit être appelé hors transaction.")
    cle = {"id": message.id, "date_reception": message.date_reception}
    soumission = Soumission(message.numero_normalise, message.texte, message.expediteur_libelle)

    with transaction.atomic():
        courant = Message.objects.select_for_update().get(**cle)
        if courant.etat in (Etat.SOUMIS, Etat.EN_ATTENTE) or courant.etat in ETATS_TERMINAUX:
            # Déjà fait (relivraison d'un message traité), ou retenu : un message
            # en attente ne repart que remis en file par celui qui l'a retenu.
            return courant
        if _appliquer_retenue(courant, source):
            return Message.objects.get(**cle)
        motif = controles.refus(courant)
        if motif:
            # Bac à sable, liste noire... : refusé ici, après la file et avant la
            # passerelle, quel que soit le chemin par lequel le message est venu.
            changer_etat(
                courant,
                Etat.REJETE,
                source=source,
                details={"motif": motif},
                motif_echec=motif,
            )
            return Message.objects.get(**cle)
        reprise = courant.soumission_debutee_le is not None and courant.date_soumission is None
        # La date de soumission est l'instant de l'appel à la passerelle, pas
        # celui où l'on en écrit le résultat : un accusé peut arriver entre deux.
        debut = courant.soumission_debutee_le if reprise else timezone.now()
        if not reprise:
            Message.objects.filter(**cle).update(soumission_debutee_le=debut, modifie_le=debut)

    if reprise:
        statut = passerelle.statut(soumission, courant.reference_smsc or None)
        if statut.sort != SortSoumission.INCONNUE:
            return _enregistrer_soumission(
                message, passerelle, statut.reference_smsc, source, debut, reprise=True
            )
        if not passerelle.retrouve_ses_soumissions:
            return _sort_inconnu(message, passerelle, source)

    reference = passerelle.soumettre(soumission)
    return _enregistrer_soumission(message, passerelle, reference, source, debut)


MOTIF_RETENU = "RETENU_PAR_LE_CLIENT"
MOTIF_ANNULE = "ANNULE_PAR_LE_CLIENT"


def _appliquer_retenue(courant: Message, source: str) -> bool:
    """Retient ou annule un message que son client a suspendu (campagne en
    pause ou annulée). `courant` est lu sous verrou. Jamais un message dont la
    soumission a commencé : il est peut-être déjà chez l'opérateur, il doit
    finir son chemin et être réglé."""
    if courant.soumission_debutee_le is not None:
        return False
    verdict = controles.retenue(courant)
    if verdict == controles.RETENIR:
        changer_etat(courant, Etat.EN_ATTENTE, source=source, details={"motif": MOTIF_RETENU})
        return True
    if verdict == controles.ANNULER:
        changer_etat(
            courant,
            Etat.ANNULE,
            source=source,
            details={"motif": MOTIF_ANNULE},
            motif_echec=MOTIF_ANNULE,
        )
        return True
    return False


def retenir(message: Message, source: str = Evenement.Source.WORKER) -> Message:
    """Applique les retenues à un message tiré de la file, avant qu'un worker
    lui réserve un créneau de débit : un message retenu ou annulé ne prend
    jamais la place d'un autre. Renvoie le message dans son état courant.

    Le cas courant ne coûte ni verrou ni transaction : le verrou n'est pris
    que si une retenue s'applique, et `soumettre` la revérifie de toute façon
    sous son propre verrou."""
    if (
        message.etat not in (Etat.RECU, Etat.EN_FILE)
        or message.soumission_debutee_le is not None
        or not controles.retenue(message)
    ):
        return message
    cle = {"id": message.id, "date_reception": message.date_reception}
    with transaction.atomic():
        courant = Message.objects.select_for_update().get(**cle)
        if courant.etat in (Etat.RECU, Etat.EN_FILE) and _appliquer_retenue(courant, source):
            return Message.objects.get(**cle)
        return courant


# Les états d'un message de campagne qui n'a pas encore été soumis.
NON_SOUMIS = (Etat.RECU, Etat.EN_FILE, Etat.EN_ATTENTE)

CHANGER_LOT = """
UPDATE journal_message AS m
SET etat = %s, motif_echec = %s, modifie_le = now()
FROM (
    SELECT id, date_reception FROM journal_message
    WHERE campagne_id = %s AND date_reception >= %s AND etat = ANY(%s)
      AND soumission_debutee_le IS NULL
    ORDER BY date_reception, id
    LIMIT %s
    FOR UPDATE SKIP LOCKED
) AS choix
WHERE m.id = choix.id AND m.date_reception = choix.date_reception
RETURNING m.id, m.date_reception, m.client_id, m.operateur, m.campagne_id,
    m.expediteur_libelle
"""


@transaction.atomic
def changer_etats_campagne(
    campagne_id,
    *,
    depuis: tuple,
    vers: str,
    depuis_le: datetime,
    source: str,
    motif: str = "",
    limite: int = 500,
) -> list[Message]:
    """Change d'état un lot de messages non soumis d'une campagne : remise en
    file à la reprise, annulation. Mêmes garanties que `changer_etat` : la
    transition est vérifiée et chaque message reçoit son événement. Un message
    dont la soumission a commencé n'est jamais touché, et un message verrouillé
    par un worker est laissé pour le lot suivant ; le worker applique lui-même
    la retenue sous ce verrou.

    `depuis_le` borne la recherche aux partitions de la campagne (sa date de
    confirmation). Renvoie les messages changés, au plus `limite`.
    """
    for etat in depuis:
        if not transition_permise(etat, vers):
            raise TransitionInterdite(campagne_id, etat, vers)
    with connection.cursor() as curseur:
        curseur.execute(CHANGER_LOT, [vers, motif, campagne_id, depuis_le, list(depuis), limite])
        lignes = curseur.fetchall()
    maintenant = timezone.now()
    details = {"motif": motif} if motif else {}
    Evenement.objects.bulk_create(
        Evenement(
            client_id=client_id,
            message_id=id_,
            etat=vers,
            horodatage=maintenant,
            source=source,
            details=details,
        )
        for id_, _, client_id, *_ in lignes
    )
    colonne = _colonne("", vers)
    if colonne:
        groupes = Counter((client_id, op, exp) for _, _, client_id, op, _, exp in lignes)
        for (client_id, operateur, expediteur), nombre in groupes.items():
            _compter(
                client_id=client_id,
                campagne_id=campagne_id,
                operateur=operateur,
                expediteur=expediteur,
                horodatage=maintenant,
                colonne=colonne,
                nombre=nombre,
            )
    return [
        Message(
            id=id_,
            date_reception=reception,
            client_id=client_id,
            operateur=operateur,
            campagne_id=campagne,
            expediteur_libelle=expediteur,
            etat=vers,
        )
        for id_, reception, client_id, operateur, campagne, expediteur in lignes
    ]


def reste_a_soumettre(campagne_id, depuis_le: datetime) -> bool:
    """Vrai si un message de la campagne n'a pas encore été soumis, retenu ou
    en cours de soumission compris. Borné aux partitions de la campagne."""
    return Message.objects.filter(
        campagne_id=campagne_id, date_reception__gte=depuis_le, etat__in=NON_SOUMIS
    ).exists()


@transaction.atomic
def _enregistrer_soumission(
    message, passerelle, reference, source, soumis_le, reprise=False
) -> Message:
    courant = _verrouiller(message)
    details = {"reference_smsc": reference}
    if reprise:
        details["reprise"] = "soumission retrouvée auprès de la passerelle, non refaite"
    changer_etat(
        courant,
        Etat.SOUMIS,
        source=source,
        horodatage=soumis_le,
        details=details,
        reference_smsc=reference,
        connecteur_id=passerelle.connecteur.id,
        date_soumission=soumis_le,
    )
    correlation.enregistrer(passerelle.connecteur.id, reference, message.id, message.date_reception)
    compteur = CLE_SOUMIS.format(connecteur_id=passerelle.connecteur.id)
    transaction.on_commit(lambda: client_redis().incr(compteur))
    return Message.objects.get(id=message.id, date_reception=message.date_reception)


@transaction.atomic
def _sort_inconnu(message, passerelle, source) -> Message:
    journal.error(
        "Soumission interrompue de %s : %s ne sait pas en dire le sort, message non resoumis.",
        message.id,
        passerelle.connecteur.nom,
        extra={"message_id": str(message.id), "connecteur_id": str(passerelle.connecteur.id)},
    )
    changer_etat(
        _verrouiller(message),
        Etat.EN_FILE,
        source=source,
        details={"motif": MOTIF_SORT_INCONNU, "connecteur": passerelle.connecteur.nom},
        motif_echec=MOTIF_SORT_INCONNU,
    )
    return Message.objects.get(id=message.id, date_reception=message.date_reception)


@transaction.atomic
def appliquer_accuse(accuse) -> bool:
    """Applique un accusé au message qu'il concerne. Renvoie vrai si l'état a
    changé ; faux pour un accusé rejoué, déjà appliqué, ou sans correspondance,
    ce dernier étant rangé parmi les orphelins et jamais jeté.

    Le message est lu par sa clé exacte, date de réception comprise : une seule
    partition ouverte, et un verrou qui sérialise deux accusés concurrents.
    """
    correspondance = correlation.retrouver(accuse.connecteur_id, accuse.reference_smsc)
    if correspondance is None:
        correlation.ranger_orphelin(accuse)
        return False
    message = (
        Message.objects.select_for_update()
        .filter(
            id=correspondance.message_id,
            date_reception=correspondance.message_date_reception,
        )
        .first()
    )
    if message is None:
        correlation.ranger_orphelin(accuse)
        return False
    if message.etat == accuse.etat:
        return False  # accusé rejoué : déjà appliqué
    try:
        changer_etat(
            message,
            accuse.etat,
            source=Evenement.Source.ACCUSE,
            horodatage=accuse.horodatage,
            details={"libelle_operateur": accuse.libelle_operateur},
            date_accuse=accuse.horodatage,
        )
    except TransitionInterdite:
        return False  # contradictoire avec l'état final déjà connu : refusé, journalisé
    delai = None
    if message.date_soumission:
        delai = (accuse.horodatage - message.date_soumission).total_seconds()
    positif = accuse.etat == Etat.LIVRE
    if positif and message.connecteur_id:
        # Les contacts sont au-dessus du journal : ils l'apprennent par le bus.
        publier(
            "journal.accuse.livre",
            client_id=message.client_id,
            objet_id=message.id,
            donnees={
                "numero_normalise": message.numero_normalise,
                # L'opérateur de la route qui a livré, non celui présumé à l'envoi.
                "operateur": Connecteur.objects.get(id=message.connecteur_id).operateur,
            },
            cle_idempotence=f"livre:{message.id}",
        )
    transaction.on_commit(
        lambda: sante.enregistrer_accuse(accuse.connecteur_id, positif, delai, accuse.horodatage)
    )
    return True


# Un message soumis sans accusé au terme de la validité de la corrélation passe
# SANS_ACCUSE. La recherche borne sa date de réception, comme toute recherche
# sur le journal : un message attend rarement plus de quelques jours entre sa
# réception et sa soumission (pause, nuit, fin de semaine) ; quatre-vingt-dix
# jours couvrent les pauses longues.
ATTENTE_MAX_AVANT_SOUMISSION = timedelta(days=90)
TAILLE_SANS_ACCUSE = 500


@transaction.atomic
def constater_sans_accuse(
    maintenant: datetime | None = None, taille: int | None = None
) -> list[tuple]:
    """Fait passer SANS_ACCUSE un lot de messages restés SOUMIS au-delà de la
    validité de la corrélation : passé ce terme, aucun accusé ne peut plus être
    rattaché au message. Renvoie (id, date_reception) des messages constatés.

    Les lignes déjà verrouillées, par un accusé en cours d'application, sont
    sautées : le prochain passage les reprendra si l'accusé ne les a pas
    conclues. Tous clients confondus : réservé aux workers.
    """
    limite = (maintenant or timezone.now()) - correlation.validite()
    messages = list(
        Message.objects.select_for_update(skip_locked=True)
        .filter(
            etat=Etat.SOUMIS,
            date_soumission__lt=limite,
            date_reception__lt=limite,
            date_reception__gte=limite - ATTENTE_MAX_AVANT_SOUMISSION,
        )
        .order_by("date_soumission")[: taille or TAILLE_SANS_ACCUSE]
    )
    for message in messages:
        changer_etat(
            message,
            Etat.SANS_ACCUSE,
            source=Evenement.Source.WORKER,
            horodatage=maintenant,
            details={"validite_heures": int(correlation.validite().total_seconds() // 3600)},
        )
    return [(message.id, message.date_reception) for message in messages]


def envoyer_unitaire(
    *,
    client,
    numero: str,
    texte: str,
    expediteur: str,
    passerelle,
    cout_unitaire: int,
    source: str = Evenement.Source.API,
) -> Message:
    """Envoi unitaire de bout en bout : normalisation du numéro, choix de
    l'encodage et décompte des segments, écriture au journal, puis soumission.

    Le message est validé au journal avant tout appel à la passerelle, jamais
    après : une coupure entre les deux laisse un message connu et non soumis,
    jamais un message parti sans trace. D'où le refus d'être appelé dans une
    transaction déjà ouverte, qui rendrait cette garantie impossible.
    """
    if connection.in_atomic_block:
        raise RuntimeError("envoyer_unitaire doit être appelé hors transaction.")
    with transaction.atomic():
        message = enregistrer_message(
            client=client,
            numero_normalise=normaliser(numero),
            operateur=passerelle.connecteur.operateur,
            expediteur_libelle=expediteur,
            texte=texte,
            cout_unitaire=cout_unitaire,
            source=source,
        )
    return soumettre(message, passerelle)


def mettre_en_file(message: Message, *, prioritaire: bool, reprise: str = "") -> str:
    """Dépose le message dans la file d'envoi de son réseau, prioritaire (code,
    transactionnel, envoi de test) ou de masse (campagne). À appeler dans la
    transaction qui a enregistré le message : la boîte d'envoi ne le publie
    qu'une fois cette transaction validée.

    `reprise` distingue une remise en file après une retenue : sans lui, la
    boîte d'envoi reconnaîtrait la première publication et ignorerait celle-ci."""
    from apps.core.files import TOPOLOGIE

    nature = "prioritaire" if prioritaire else "masse"
    return publier(
        TOPOLOGIE.nom(f"envoi.{nature}.{message.operateur.lower()}"),
        echange=TOPOLOGIE.echange,
        client_id=message.client_id,
        objet_id=message.id,
        donnees={
            "message_id": str(message.id),
            "date_reception": message.date_reception.isoformat(),
        },
        cle_idempotence=f"envoi:{message.id}:{reprise}" if reprise else f"envoi:{message.id}",
    )


# Désinscriptions reçues sans aucun envoi correspondant : signalées à
# l'exploitation par les métriques, gardées en base avec le message reçu.
CLE_DESINSCRIPTIONS_SANS_EMETTEUR = "sms:metriques:desinscriptions_sans_emetteur"
DESINSCRIPTION_RAPPROCHEE = "journal.desinscription.rapprochee"


def texte_de_confirmation(mot_cle: str, expediteur: str) -> str:
    """Réponse automatique du mot-clé, rendue pour un expéditeur. Vide si le
    mot-clé n'en porte pas."""
    modele = (
        MotCle.objects.filter(client__isnull=True, mot_cle=mot_cle)
        .values_list("reponse_automatique", flat=True)
        .first()
    )
    return (modele or "").replace("{expediteur}", expediteur)


@transaction.atomic
def rapprocher_desinscription(
    *,
    entrant_id,
    numero_expediteur: str,
    texte: str,
    recu_le: datetime,
    mot_cle: str = "",
    operateur: str = "",
) -> list:
    """Rapproche un STOP des clients qui ont écrit à ce numéro dans les
    soixante-douze heures qui précèdent sa réception (T-70).

    Un seul client : le STOP s'applique à lui. Plusieurs : il s'applique à
    chacun, une désinscription par client, car dans le doute la désinscription
    l'emporte. Aucun : le message reçu est gardé et signalé, jamais jeté. Les
    contacts, au-dessus du journal, l'apprennent par le bus, avec la
    confirmation à renvoyer, qui nomme l'expéditeur que le client a employé.
    Renvoie les clients retenus."""
    try:
        numero = normaliser(numero_expediteur)
    except NumeroInvalide:
        numero, touches = "", {}
    else:
        touches = clients_touches(numero, recu_le)
    for client_id, expediteur in touches.items():
        publier(
            DESINSCRIPTION_RAPPROCHEE,
            client_id=client_id,
            objet_id=entrant_id,
            donnees={
                "numero_normalise": numero,
                "texte": texte,
                "recu_le": recu_le.isoformat(),
                "operateur": operateur,
                "confirmation": texte_de_confirmation(mot_cle, expediteur),
            },
            cle_idempotence=f"desinscription:{entrant_id}:{client_id}",
        )
    clients = sorted(touches, key=str)
    marquer_rapproche(entrant_id, clients)
    if not clients:
        journal.warning(
            "Désinscription sans envoi correspondant dans les 72 heures : entrant %s.",
            entrant_id,
        )
        transaction.on_commit(lambda: client_redis().incr(CLE_DESINSCRIPTIONS_SANS_EMETTEUR))
    return clients


def confirmer_desinscription(
    *, client_id, numero_normalise: str, operateur: str, texte: str
) -> Message | None:
    """Envoie au numéro la confirmation de sa désinscription (T-71), une fois
    l'inscription en liste noire faite. Message système : sous le numéro court,
    à coût nul, sans réservation ni écriture au registre de crédits, et soumis
    malgré la liste noire, parce que c'est une obligation et non un service
    facturé. Il part donc même si le client n'a plus de crédit. Passe par la
    file prioritaire, jamais retenu par la fenêtre légale."""
    if not texte:
        journal.warning("Aucune confirmation de désinscription à envoyer vers %s.", operateur)
        return None
    with transaction.atomic():
        message = enregistrer_message(
            client=Client.objects.get(id=client_id),
            numero_normalise=numero_normalise,
            operateur=operateur,
            expediteur_libelle=numero_court(),
            texte=texte,
            cout_unitaire=0,
            nature=Message.Nature.SYSTEME,
            source=Evenement.Source.WORKER,
        )
        mettre_en_file(message, prioritaire=True)
    return message


# Recalcul des compteurs d'une campagne (T-91)

# Les compteurs reconstruits depuis les événements, heure par heure, comme
# `changer_etat` les écrit : l'heure est celle de la transition. Un REJETE après
# soumission est un échec, avant, un refus ; la date de soumission le dit, car
# SOUMIS précède toujours un rejet de l'opérateur.
RECOMPTER = """
INSERT INTO journal_compteur (
    client_id, campagne_id, operateur, expediteur_libelle, heure,
    emis, livres, echecs, sans_accuse, refuses, annules, cout, latence_cumulee_ms, latences
)
SELECT
    m.client_id, m.campagne_id, m.operateur, m.expediteur_libelle,
    date_trunc('hour', e.horodatage),
    count(*) FILTER (WHERE e.etat = 'SOUMIS'),
    count(*) FILTER (WHERE e.etat = 'LIVRE'),
    count(*) FILTER (
        WHERE e.etat IN ('NON_LIVRE', 'EXPIRE')
           OR (e.etat = 'REJETE' AND m.date_soumission IS NOT NULL)
    ),
    count(*) FILTER (WHERE e.etat = 'SANS_ACCUSE'),
    count(*) FILTER (WHERE e.etat = 'REJETE' AND m.date_soumission IS NULL),
    count(*) FILTER (WHERE e.etat = 'ANNULE'),
    coalesce(sum(m.cout_total) FILTER (WHERE e.etat = 'SOUMIS'), 0),
    coalesce(sum(
        greatest(0, floor(extract(epoch FROM e.horodatage - m.date_soumission) * 1000))
    ) FILTER (WHERE e.etat = 'LIVRE' AND m.date_soumission IS NOT NULL), 0),
    count(*) FILTER (WHERE e.etat = 'LIVRE' AND m.date_soumission IS NOT NULL)
FROM journal_message AS m
JOIN journal_evenement AS e ON e.message_id = m.id AND e.horodatage >= %s
WHERE m.client_id = %s AND m.campagne_id = %s AND m.date_reception >= %s
  AND e.etat IN ('SOUMIS', 'LIVRE', 'NON_LIVRE', 'EXPIRE', 'REJETE', 'SANS_ACCUSE', 'ANNULE')
GROUP BY 1, 2, 3, 4, 5
"""
VERROUILLER_CAMPAGNE = """
SELECT 1 FROM journal_message
WHERE client_id = %s AND campagne_id = %s AND date_reception >= %s
FOR UPDATE
"""
# Marge sur l'horodatage des événements : un accusé porte l'heure annoncée par
# l'opérateur, qui peut précéder de peu celle de nos serveurs.
MARGE_EVENEMENTS = timedelta(days=1)


@transaction.atomic
def recompter(client_id, campagne_id, depuis: datetime) -> int:
    """Réécrit les compteurs d'une campagne depuis le journal. `depuis` borne
    les partitions lues : la confirmation de la campagne, avant laquelle aucun
    de ses messages n'est reçu.

    Remplace, n'ajoute pas : rejoué, le recalcul rend les mêmes lignes, sans
    double comptage. Les messages de la campagne sont verrouillés le temps du
    recalcul ; un changement d'état concurrent, qui verrouille son message
    avant d'écrire, attend la fin. Renvoie le nombre de lignes écrites."""
    with connection.cursor() as curseur:
        curseur.execute(VERROUILLER_CAMPAGNE, [client_id, campagne_id, depuis])
        Compteur.objects.filter(client_id=client_id, campagne_id=campagne_id).delete()
        curseur.execute(RECOMPTER, [depuis - MARGE_EVENEMENTS, client_id, campagne_id, depuis])
        return curseur.rowcount
