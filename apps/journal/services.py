"""Porte d'écriture du module journal."""

import logging
from datetime import datetime

from django.db import connection, connections, transaction
from django.utils import timezone

from apps.core import controles
from apps.core.encodage import analyser
from apps.core.etats import ETATS_TERMINAUX, Etat, transition_permise
from apps.core.evenements import publier
from apps.core.numeros import normaliser
from apps.core.redis import client_redis
from apps.telecom import correlation, sante
from apps.telecom.models import Connecteur
from apps.telecom.passerelles import SortSoumission, Soumission

from .models import Destinataire, Evenement, Message

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
    un événement daté. Une transition interdite est journalisée et refusée.

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
        if courant.etat == Etat.SOUMIS or courant.etat in ETATS_TERMINAUX:
            return courant  # déjà fait : relivraison d'un message traité
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


def mettre_en_file(message: Message, *, prioritaire: bool) -> str:
    """Dépose le message dans la file d'envoi de son réseau, prioritaire (code,
    transactionnel, envoi de test) ou de masse (campagne). À appeler dans la
    transaction qui a enregistré le message : la boîte d'envoi ne le publie
    qu'une fois cette transaction validée."""
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
        cle_idempotence=f"envoi:{message.id}",
    )
