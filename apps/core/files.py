"""Échanges et files RabbitMQ, et paliers de réessai.

Un échange de type sujet, `sms`, et douze files séparées par nature de trafic
et par réseau destinataire (voir l'architecture, « Files, workers et reprise »).
Chaque file est liée à `sms` par son propre nom.

Le réessai se fait sans minuterie applicative. Un message en échec est publié
sur l'échange `sms.reessai`, de type en-têtes, avec pour clé de routage le nom
de sa file d'origine et pour en-tête le palier atteint. Chaque palier est une
file dotée d'une durée de vie, qui renvoie ses messages expirés vers l'échange
par défaut : la clé de routage d'origine les ramène dans leur file. Après le
dernier palier, un nouvel échec envoie le message dans `rebut`, que personne ne
consomme et qu'un humain inspecte.
"""

from dataclasses import dataclass, field

import aio_pika

OPERATEURS = ("orange", "moov", "telecel")

FILES = (
    *(f"envoi.prioritaire.{operateur}" for operateur in OPERATEURS),
    *(f"envoi.masse.{operateur}" for operateur in OPERATEURS),
    "import.contacts",
    "accuses.entrants",
    "messages.entrants",
    "webhooks.sortants",
    "courriels.sortants",
    "rebut",
)

# Files abonnées à des événements du bus (échange `evenements`) : nom de la
# file et motif de clé de routage.
ABONNEMENTS = {
    "contacts.operateurs": "journal.accuse.livre",
    "journal.desinscriptions": "telecom.desinscription.recue",
    "contacts.desinscriptions": "journal.desinscription.rapprochee",
}

# Une minute, cinq, trente, deux heures, six heures, en millisecondes.
PALIERS_MS = (60_000, 300_000, 1_800_000, 7_200_000, 21_600_000)

# Sans le préfixe x- : RabbitMQ 4 traite les en-têtes x- comme ses propres
# annotations, et un message renvoyé par un palier revenait avec les valeurs de
# sa première publication, si bien que le compteur ne montait jamais.
ENTETE_TENTATIVE = "sms-tentative"
ENTETE_PALIER = "sms-palier"
ENTETE_FILE = "sms-file-origine"
ENTETE_ERREUR = "sms-erreur"


@dataclass(frozen=True)
class Topologie:
    """Noms et durées, préfixables pour que les tests disposent de la leur."""

    prefixe: str = ""
    paliers_ms: tuple[int, ...] = PALIERS_MS
    files: tuple[str, ...] = field(default=FILES)

    def nom(self, base: str) -> str:
        return f"{self.prefixe}{base}"

    @property
    def echange(self) -> str:
        return self.nom("sms")

    @property
    def echange_reessai(self) -> str:
        return self.nom("sms.reessai")

    @property
    def rebut(self) -> str:
        return self.nom("rebut")

    def file_palier(self, palier: int) -> str:
        return self.nom(f"reessai.{palier}")


TOPOLOGIE = Topologie()


async def declarer(canal, topologie: Topologie = TOPOLOGIE) -> None:
    """Déclare échanges, files et paliers. Idempotent : à lancer au démarrage."""
    echange = await canal.declare_exchange(
        topologie.echange, aio_pika.ExchangeType.TOPIC, durable=True
    )
    echange_reessai = await canal.declare_exchange(
        topologie.echange_reessai, aio_pika.ExchangeType.HEADERS, durable=True
    )
    for base in topologie.files:
        file = await canal.declare_queue(topologie.nom(base), durable=True)
        if base != "rebut":
            await file.bind(echange, routing_key=topologie.nom(base))
    evenements = await canal.declare_exchange(
        topologie.nom("evenements"), aio_pika.ExchangeType.TOPIC, durable=True
    )
    for base, motif in ABONNEMENTS.items():
        file = await canal.declare_queue(topologie.nom(base), durable=True)
        await file.bind(evenements, routing_key=motif)
    for palier, duree_ms in enumerate(topologie.paliers_ms, start=1):
        file = await canal.declare_queue(
            topologie.file_palier(palier),
            durable=True,
            arguments={"x-message-ttl": duree_ms, "x-dead-letter-exchange": ""},
        )
        await file.bind(echange_reessai, arguments={"x-match": "all", ENTETE_PALIER: palier})


async def echouer(
    canal,
    message: aio_pika.abc.AbstractIncomingMessage,
    file_origine: str,
    erreur: str,
    topologie: Topologie = TOPOLOGIE,
) -> str:
    """Reprogramme un message en échec au palier suivant, ou le met au rebut.

    Le message d'origine n'est acquitté qu'une fois sa copie publiée : une mort
    entre les deux produit au pire un doublon, que l'idempotence absorbe.
    Renvoie le nom de la file où la copie est partie.
    """
    # Les en-têtes x-death et x-first-death / x-last-death appartiennent au
    # courtier, qui les tient à jour lui-même : les recopier fait perdre la copie.
    entetes = {
        cle: valeur
        for cle, valeur in (message.headers or {}).items()
        if cle != "x-death" and not cle.startswith(("x-first-death", "x-last-death"))
    }
    tentative = int(entetes.get(ENTETE_TENTATIVE, 0)) + 1
    entetes |= {ENTETE_TENTATIVE: tentative, ENTETE_FILE: file_origine, ENTETE_ERREUR: erreur}
    au_rebut = tentative > len(topologie.paliers_ms)
    if au_rebut:
        entetes.pop(ENTETE_PALIER, None)
    else:
        entetes[ENTETE_PALIER] = tentative
    copie = aio_pika.Message(
        message.body,
        headers=entetes,
        message_id=message.message_id,
        content_type=message.content_type,
        delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
    )
    if au_rebut:
        await canal.default_exchange.publish(copie, routing_key=topologie.rebut)
        destination = topologie.rebut
    else:
        echange = await canal.get_exchange(topologie.echange_reessai)
        await echange.publish(copie, routing_key=file_origine)
        destination = topologie.file_palier(tentative)
    await message.ack()
    return destination
