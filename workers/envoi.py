"""Worker d'envoi : choix du connecteur, débit, soumission, suite donnée aux échecs.

Des groupes distincts en exploitation (T-63) : un groupe sur les files
prioritaires, pour qu'un code à usage unique ne passe jamais derrière une
campagne, et un groupe de masse par réseau. SMS_FILES_ENVOI choisit les files
d'un processus ; SMS_PREFETCH_ENVOI son nombre de messages en cours, à aligner
sur le débit accordé par l'opérateur. Chaque soumission retient d'abord un
créneau de débit du connecteur (`telecom.debit`), partagé par tous les
processus, et l'attend juste avant l'appel à la passerelle.

Chaque message de file porte l'identifiant et la date de réception du message
au journal. La soumission passe par `journal.services.soumettre`, qui ne soumet
jamais deux fois : une relivraison par le courtier est sans danger.
"""

import json
import os
import time

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

    def __init__(self, topologie=None, files=None, prefetch=None):
        super().__init__(topologie)
        if files is not None:
            self.files = tuple(files)
        self.prefetch = prefetch or int(os.environ.get("SMS_PREFETCH_ENVOI", self.prefetch))
        self.passerelles = {}

    def _passerelle(self, connecteur):
        from apps.telecom.passerelles import passerelle_pour

        if connecteur.id not in self.passerelles:
            self.passerelles[connecteur.id] = passerelle_pour(connecteur)
        return self.passerelles[connecteur.id]

    def _soumettre(self, donnees: dict, avec_creneau: bool = False) -> None:
        """Soumet le message de file. Avec le créneau de débit, le créneau est
        retenu et attendu juste avant l'appel à la passerelle, dans le fil qui
        soumet : deux soumissions d'un même processus ne se rapprochent jamais
        à moins de 1/débit. Un message déjà soumis (relivraison) ne retient
        aucun créneau."""
        from django.utils.dateparse import parse_datetime

        from apps.core.etats import Etat
        from apps.journal.models import Evenement, Message
        from apps.journal.services import retenir, soumettre
        from apps.telecom import debit
        from apps.telecom.services import connecteur_pour

        message = retenir(
            Message.objects.get(
                id=donnees["message_id"], date_reception=parse_datetime(donnees["date_reception"])
            )
        )
        if message.etat in (Etat.EN_ATTENTE, Etat.ANNULE):
            # Retenu (campagne en pause) ou annulé : ni créneau ni soumission.
            return
        connecteur = connecteur_pour(message.operateur)
        if connecteur is None:
            raise LookupError(f"aucune liaison active pour {message.operateur}")
        a_soumettre = message.etat in (Etat.RECU, Etat.EN_FILE)
        if avec_creneau and a_soumettre and message.soumission_debutee_le is None:
            time.sleep(debit.creneau(connecteur))
        self._solder(
            soumettre(message, self._passerelle(connecteur), source=Evenement.Source.WORKER)
        )

    @staticmethod
    def _solder(message) -> None:
        """Consommation à la soumission, ou libération d'un refus. Idempotent :
        une relivraison après une soumission déjà faite ne consomme pas deux fois.
        Un message de campagne non soumis ne coûte rien et ne touche pas à la
        réservation de sa campagne, qui peut déjà être close (T-64)."""
        from apps.credits.services import SansReservation, solder_message

        if message.campagne_id and message.date_soumission is None:
            return

        try:
            solder_message(
                client_id=message.client_id,
                message_id=message.id,
                campagne_id=message.campagne_id,
                montant=message.cout_total,
                soumis=message.date_soumission is not None,
            )
        except SansReservation:
            journal.warning("Message %s envoyé sans réservation de crédit.", message.id)

    async def traiter(self, message, canal, file_origine: str) -> None:
        from asgiref.sync import sync_to_async

        from apps.core.files import echouer
        from apps.telecom.passerelles import PasserelleIndisponible, SoumissionRejetee

        corps = json.loads(message.body)
        donnees = corps.get("donnees", corps)
        try:
            await sync_to_async(self._soumettre)(donnees, avec_creneau=True)
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
        WorkerEnvoi._solder(courant)


if __name__ == "__main__":
    lancer(WorkerEnvoi)
