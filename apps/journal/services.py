"""Porte d'écriture du module journal."""

from datetime import datetime

from django.db import connections, transaction
from django.utils import timezone

from apps.core.encodage import analyser

from .models import Destinataire, Evenement, Message

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
