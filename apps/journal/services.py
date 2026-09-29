"""Porte d'écriture du module journal."""

from datetime import datetime

from django.db import connections, transaction
from django.utils import timezone

from apps.core.encodage import analyser
from apps.telecom import correlation
from apps.telecom.passerelles import Soumission

from .models import ETATS_TERMINAUX, Destinataire, Etat, Evenement, Message

TABLES_PARTITIONNEES = ("journal_message", "journal_evenement")


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


@transaction.atomic
def soumettre(message: Message, passerelle, source: str = Evenement.Source.WORKER) -> Message:
    """Soumet un message à la passerelle et le passe SOUMIS.

    Le marqueur de soumission interrompue et la reprise sans doublon sont
    l'objet de T-25 ; ici le marqueur est posé, la reprise n'est pas encore là.
    """
    cle = {"id": message.id, "date_reception": message.date_reception}
    maintenant = timezone.now()
    Message.objects.filter(**cle).update(soumission_debutee_le=maintenant)
    reference = passerelle.soumettre(
        Soumission(message.numero_normalise, message.texte, message.expediteur_libelle)
    )
    Message.objects.filter(**cle).update(
        reference_smsc=reference,
        connecteur_id=passerelle.connecteur.id,
        date_soumission=maintenant,
        etat=Etat.SOUMIS,
        modifie_le=maintenant,
    )
    correlation.enregistrer(passerelle.connecteur.id, reference, message.id, message.date_reception)
    Evenement.objects.create(
        client_id=message.client_id,
        message_id=message.id,
        etat=Etat.SOUMIS,
        horodatage=maintenant,
        source=source,
        details={"reference_smsc": reference},
    )
    return Message.objects.get(**cle)


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
    if message.etat in ETATS_TERMINAUX:
        return False
    Message.objects.filter(id=message.id, date_reception=message.date_reception).update(
        etat=accuse.etat, date_accuse=accuse.horodatage, modifie_le=timezone.now()
    )
    Evenement.objects.create(
        client_id=message.client_id,
        message_id=message.id,
        etat=accuse.etat,
        horodatage=accuse.horodatage,
        source=Evenement.Source.ACCUSE,
        details={"libelle_operateur": accuse.libelle_operateur},
    )
    return True
