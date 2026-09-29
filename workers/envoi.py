"""Worker d'envoi : choix du connecteur, soumission, suite donnée aux échecs.

Deux groupes au moins en exploitation, l'un sur les files prioritaires, l'autre
sur les files de masse, pour qu'un code à usage unique ne passe jamais derrière
une campagne. SMS_FILES_ENVOI choisit les files d'un processus.

Chaque message de file porte l'identifiant et la date de réception du message
au journal. La soumission passe par `journal.services.soumettre`, qui ne soumet
jamais deux fois : une relivraison par le courtier est sans danger.
"""

import json
import os

from apps.core.files import FILES

from .base import Worker, journal, lancer


class WorkerEnvoi(Worker):
    nom = "envoi"
    prefetch = 10
    files = tuple(
        os.environ.get("SMS_FILES_ENVOI", "").split(",")
        if os.environ.get("SMS_FILES_ENVOI")
        else [f for f in FILES if f.startswith("envoi.")]
    )

    def __init__(self, topologie=None):
        super().__init__(topologie)
        self.passerelles = {}

    def _passerelle(self, connecteur):
        from apps.telecom.passerelles import passerelle_pour

        if connecteur.id not in self.passerelles:
            self.passerelles[connecteur.id] = passerelle_pour(connecteur)
        return self.passerelles[connecteur.id]

    def _soumettre(self, donnees: dict) -> None:
        from django.utils.dateparse import parse_datetime

        from apps.journal.models import Evenement, Message
        from apps.journal.services import soumettre
        from apps.telecom.services import connecteur_pour

        message = Message.objects.get(
            id=donnees["message_id"], date_reception=parse_datetime(donnees["date_reception"])
        )
        connecteur = connecteur_pour(message.operateur)
        if connecteur is None:
            raise LookupError(f"aucune liaison active pour {message.operateur}")
        soumettre(message, self._passerelle(connecteur), source=Evenement.Source.WORKER)

    async def traiter(self, message, canal, file_origine: str) -> None:
        from asgiref.sync import sync_to_async

        from apps.core.files import echouer
        from apps.telecom.passerelles import PasserelleIndisponible, SoumissionRejetee

        corps = json.loads(message.body)
        donnees = corps.get("donnees", corps)
        try:
            await sync_to_async(self._soumettre)(donnees)
        except (PasserelleIndisponible, LookupError) as erreur:
            # Liaison indisponible : le message attend le palier suivant.
            journal.warning("Envoi différé de %s : %s", donnees.get("message_id"), erreur)
            await echouer(canal, message, file_origine, str(erreur)[:200], self.topologie)
            return
        except SoumissionRejetee as rejet:
            await sync_to_async(self._rejeter)(donnees, rejet)
        except Exception as erreur:
            journal.exception("Envoi en échec : %s", donnees.get("message_id"))
            await echouer(canal, message, file_origine, repr(erreur)[:200], self.topologie)
            return
        await message.ack()

    @staticmethod
    def _rejeter(donnees: dict, rejet) -> None:
        from django.db import transaction
        from django.utils.dateparse import parse_datetime

        from apps.core.etats import Etat
        from apps.journal.models import Evenement, Message
        from apps.journal.services import changer_etat

        with transaction.atomic():
            courant = Message.objects.select_for_update().get(
                id=donnees["message_id"], date_reception=parse_datetime(donnees["date_reception"])
            )
            changer_etat(
                courant,
                Etat.REJETE,
                source=Evenement.Source.WORKER,
                details={"code": rejet.code, "motif": rejet.motif},
                motif_echec=f"{rejet.code} {rejet.motif}"[:100],
            )


if __name__ == "__main__":
    lancer(WorkerEnvoi)
