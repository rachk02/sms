"""Point de contrôle du lot 1 : N messages à travers la chaîne, contre le simulateur.

    SMS_CONNEXION_PAR_DEFAUT=WORKER DJANGO_SETTINGS_MODULE=config.settings.recette \\
        uv run python manage.py recette_charge --messages 10000

Chaque message est écrit au journal, publié dans la file d'envoi de masse de son
réseau, consommé, soumis par la couche d'abstraction au connecteur simulé de
son opérateur, corrélé en base et dans Redis, puis passé à son état final par
son accusé. Le rapport vérifie qu'aucun message n'est perdu ni bloqué dans un
état intermédiaire, et que les compteurs correspondent au nombre envoyé.

Le simulateur tient ses accusés en mémoire : la chaîne tourne donc dans un seul
processus. Les workers d'envoi et d'accusés séparés arrivent avec le lot 2.
"""

import asyncio
import json
import time
import uuid
from collections import Counter

import aio_pika
from asgiref.sync import sync_to_async
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from apps.comptes.models import Client
from apps.core import bus
from apps.core.etats import ETATS_TERMINAUX
from apps.core.files import TOPOLOGIE
from apps.core.redis import client_redis
from apps.journal.models import Evenement, Message
from apps.journal.services import CLE_SOUMIS, appliquer_accuse, enregistrer_message, soumettre
from apps.telecom.models import Connecteur, Correlation
from apps.telecom.passerelles import passerelle_pour

TAILLE_LOT = 500
DELAI_MAX_S = 600


class Command(BaseCommand):
    help = "Fait traverser la chaîne complète à N messages contre le simulateur, puis vérifie."

    def add_arguments(self, parser):
        parser.add_argument("--messages", type=int, default=10_000)

    def handle(self, *args, messages, **options):
        if not settings.SETTINGS_MODULE.endswith(".recette"):
            raise CommandError("Réservé à config.settings.recette.")
        if (
            connections["default"].settings_dict["USER"]
            != connections["worker"].settings_dict["USER"]
        ):
            raise CommandError("À lancer avec SMS_CONNEXION_PAR_DEFAUT=WORKER, comme un worker.")
        rapport = asyncio.run(self._executer(messages))
        for ligne in rapport["lignes"]:
            self.stdout.write(ligne)
        if rapport["fautes"]:
            for faute in rapport["fautes"]:
                self.stdout.write(f"  FAUTE {faute}")
            raise CommandError(f"{len(rapport['fautes'])} contrôle(s) en échec.")
        self.stdout.write("Point de contrôle atteint : aucune perte, aucun message bloqué.")

    async def _executer(self, nombre: int) -> dict:
        connecteurs = await sync_to_async(
            lambda: list(
                Connecteur.objects.filter(type=Connecteur.Type.SIMULE, nom__startswith="recette-")
            )
        )()
        clients = await sync_to_async(
            # Hors du bac à sable : un client au palier Découverte n'envoie qu'à ses
            # numéros de test, ce que ce contrôle de charge ne vise pas.
            lambda: list(
                Client.objects.filter(raison_sociale__startswith="Recette ").exclude(
                    palier=Client.Palier.DECOUVERTE
                )
            )
        )()
        if len(connecteurs) != 3 or not clients:
            raise CommandError("Jeu d'essai absent : lancer scripts/preparer_recette.sh.")
        passerelles = {c.operateur: passerelle_pour(c) for c in connecteurs}
        operateurs = sorted(passerelles)
        campagne = uuid.uuid7()
        compteurs_avant = await sync_to_async(self._compteurs_soumis)(connecteurs)
        debut = time.monotonic()

        connexion = await bus.connecter()
        async with connexion:
            canal = await connexion.channel()
            await canal.set_qos(prefetch_count=200)
            echange = await canal.get_exchange(TOPOLOGIE.echange)

            # 1. Écriture au journal et publication dans la file de son réseau.
            publies = Counter()
            for depart in range(0, nombre, TAILLE_LOT):
                lot = await sync_to_async(self._ecrire_lot)(
                    campagne, clients, operateurs, depart, min(TAILLE_LOT, nombre - depart)
                )
                for message_id, date_reception, operateur in lot:
                    corps = json.dumps({"message_id": message_id, "date_reception": date_reception})
                    await echange.publish(
                        aio_pika.Message(
                            corps.encode(),
                            message_id=message_id,
                            delivery_mode=aio_pika.DeliveryMode.PERSISTENT,
                        ),
                        routing_key=TOPOLOGIE.nom(f"envoi.masse.{operateur.lower()}"),
                    )
                    publies[operateur] += 1
            publication = time.monotonic() - debut

            # 2. Consommation des files d'envoi, exactement ce qui y a été publié, et
            # soumission au simulateur de chaque réseau.
            soumis, limite = 0, time.monotonic() + DELAI_MAX_S
            for operateur in operateurs:
                file = await canal.get_queue(TOPOLOGIE.nom(f"envoi.masse.{operateur.lower()}"))
                recus = 0
                while recus < publies[operateur] and time.monotonic() < limite:
                    recu = await file.get(fail=False)
                    if recu is None:
                        await asyncio.sleep(0.05)
                        continue
                    donnees = json.loads(recu.body)
                    await sync_to_async(self._soumettre)(donnees, passerelles[operateur])
                    await recu.ack()
                    recus += 1
                    soumis += 1
                    if soumis % 1_000 == 0:
                        self.stdout.write(f"  {soumis} soumis")
            soumission = time.monotonic() - debut

            # 3. Accusés : attendre le délai de chaque simulateur, puis appliquer.
            appliques, limite = 0, time.monotonic() + DELAI_MAX_S
            while appliques < soumis and time.monotonic() < limite:
                for passerelle in passerelles.values():
                    for accuse in passerelle.accuses_dus():
                        appliques += await sync_to_async(appliquer_accuse)(accuse)
                await asyncio.sleep(0.5)
            files_restantes = {
                operateur: await self._profondeur(canal, f"envoi.masse.{operateur.lower()}")
                for operateur in operateurs
            }
        duree = time.monotonic() - debut

        return await sync_to_async(self._verifier)(
            campagne,
            nombre,
            soumis,
            appliques,
            connecteurs,
            compteurs_avant,
            files_restantes,
            {"publication": publication, "soumission": soumission, "total": duree},
        )

    @staticmethod
    async def _profondeur(canal, base) -> int:
        """0 si la file est vide. Lecture directe plutôt que déclaration passive,
        dont le canal robuste d'aio-pika garde le résultat en cache."""
        file = await canal.get_queue(TOPOLOGIE.nom(base))
        reste = await file.get(fail=False)
        if reste is None:
            return 0
        await reste.nack(requeue=True)
        return 1

    @staticmethod
    def _ecrire_lot(campagne, clients, operateurs, depart, taille):
        lot = []
        with transaction.atomic():
            for rang in range(depart, depart + taille):
                operateur = operateurs[rang % len(operateurs)]
                message = enregistrer_message(
                    client=clients[rang % len(clients)],
                    numero_normalise=f"+2267{rang:07d}",
                    operateur=operateur,
                    expediteur_libelle="RECETTE",
                    texte=f"Message de recette {rang}",
                    cout_unitaire=10,
                    campagne_id=campagne,
                    contact_id=uuid.uuid7(),
                    source=Evenement.Source.WORKER,
                )
                lot.append((str(message.id), message.date_reception.isoformat(), operateur))
        return lot

    @staticmethod
    def _soumettre(donnees, passerelle):
        message = Message.objects.get(
            id=donnees["message_id"], date_reception=parse_datetime(donnees["date_reception"])
        )
        soumettre(message, passerelle)

    @staticmethod
    def _compteurs_soumis(connecteurs) -> int:
        cles = [CLE_SOUMIS.format(connecteur_id=c.id) for c in connecteurs]
        return sum(int(valeur or 0) for valeur in client_redis().mget(cles))

    def _verifier(
        self, campagne, nombre, soumis, appliques, connecteurs, avant, files_restantes, durees
    ) -> dict:
        jour = timezone.now() - timezone.timedelta(days=1)
        messages = Message.objects.filter(campagne_id=campagne, date_reception__gte=jour)
        etats = Counter(messages.values_list("etat", flat=True))
        ecrits = sum(etats.values())
        bloques = sum(n for etat, n in etats.items() if etat not in ETATS_TERMINAUX)
        compteur_redis = self._compteurs_soumis(connecteurs) - avant
        references = messages.values_list("reference_smsc", flat=True)
        correlations = Correlation.objects.filter(reference_smsc__in=list(references)).count()
        evenements = Evenement.objects.filter(
            message_id__in=messages.values("id"), horodatage__gte=jour
        ).count()

        fautes = []
        if ecrits != nombre:
            fautes.append(f"{ecrits} messages au journal pour {nombre} envoyés")
        if soumis != nombre:
            fautes.append(f"{soumis} messages soumis pour {nombre} envoyés")
        if bloques:
            fautes.append(f"{bloques} messages bloqués dans un état intermédiaire")
        if appliques != nombre:
            fautes.append(f"{appliques} accusés appliqués pour {nombre} messages")
        if compteur_redis != nombre:
            fautes.append(f"compteur des soumis à {compteur_redis} pour {nombre}")
        if correlations != nombre:
            fautes.append(f"{correlations} corrélations pour {nombre} messages")
        if evenements != 3 * nombre:
            fautes.append(f"{evenements} événements pour {3 * nombre} attendus")
        if any(files_restantes.values()):
            fautes.append(f"files d'envoi non vides : {files_restantes}")

        lignes = [
            f"Campagne de recette {campagne} : {nombre} messages.",
            f"  États finaux : {dict(sorted(etats.items()))}",
            f"  Soumis {soumis}, accusés appliqués {appliques}, compteur Redis {compteur_redis},"
            f" corrélations {correlations}, événements {evenements}.",
            f"  Durées : publication {durees['publication']:.0f} s, soumission"
            f" {durees['soumission']:.0f} s, total {durees['total']:.0f} s.",
        ]
        return {"lignes": lignes, "fautes": fautes}
