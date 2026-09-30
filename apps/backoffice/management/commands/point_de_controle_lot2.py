"""Point de contrôle du lot 2 : le scénario complet, sans interface.

    SMS_CONNEXION_PAR_DEFAUT=WORKER uv run python manage.py point_de_controle_lot2

Prérequis : docker compose --profile passerelle up -d (Jasmin et SMPPSim).

La commande lance la plateforme en processus séparés, comme en exploitation :
le service web (point d'entrée des rappels, rôle applicatif), le worker
d'envoi, le worker accusés et l'ordonnanceur (rôle worker). Puis elle joue :

1. N messages déposés dans la file d'envoi par la boîte d'envoi, relayés,
   soumis par Jasmin à SMPPSim, accusés par rappel jusqu'à leur état final ;
2. la coupure de la liaison (SMPPSim arrêté), constatée par la surveillance,
   des messages envoyés pendant la coupure, le rétablissement, et ces messages
   partis à leur tour ;
3. un SMS entrant émis par SMPPSim, relayé jusqu'en base.

Elle vérifie qu'aucun message ne reste dans un état intermédiaire et que le
compte des états finaux égale le compte des messages envoyés, puis rend son
rapport. C'est ce scénario qui sera rejoué contre une liaison réelle.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.parse
import urllib.request
import uuid
from collections import Counter
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import connections, transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.etats import ETATS_TERMINAUX
from apps.core.evenements import publier
from apps.core.files import TOPOLOGIE
from apps.core.models import EvenementSortant
from apps.journal.models import Message
from apps.journal.services import enregistrer_message
from apps.telecom.models import Connecteur, MessageEntrant, Operateur
from apps.telecom.numero_court import numero_court
from apps.telecom.rappels import jeton

RACINE = Path(settings.BASE_DIR)
FILE_ENVOI = "envoi.masse.orange"
NOM_CONNECTEUR = "controle-lot2-orange"


class Echec(Exception):
    pass


class Command(BaseCommand):
    help = "Joue le point de contrôle du lot 2 contre Jasmin et SMPPSim, et rend son rapport."

    def add_arguments(self, parser):
        parser.add_argument("--messages", type=int, default=100)
        parser.add_argument("--pendant-coupure", type=int, default=10)
        parser.add_argument("--port", type=int, default=8765)
        parser.add_argument("--rapport", help="écrit aussi le rapport dans ce fichier")

    def handle(self, *args, messages, pendant_coupure, port, rapport, **options):
        if (
            connections["default"].settings_dict["USER"]
            != connections["worker"].settings_dict["USER"]
        ):
            raise CommandError("À lancer avec SMS_CONNEXION_PAR_DEFAUT=WORKER.")
        self._verifier_prerequis()
        self.journaux = Path(tempfile.mkdtemp(prefix="controle-lot2-"))
        self.processus = {}
        self.lignes, self.fautes = [], []
        self.debut = time.monotonic()
        desactives = []
        try:
            connecteur, desactives = self._preparer_connecteur()
            client = self._client()
            base_rappels = f"http://host.docker.internal:{port}/rappels/jasmin"
            self._configurer_jasmin(f"{base_rappels}/{connecteur.id}?jeton={jeton(connecteur.id)}")
            self._lancer(port, base_rappels)

            lot_a = self._envoyer(client, messages, "A")
            self._attendre_etats_finaux(lot_a, 180, "lot A")

            self._couper_et_retablir(connecteur, client, pendant_coupure)

            self._message_entrant()
        except Echec as echec:
            self.fautes.append(str(echec))
        finally:
            self._arreter()
            Connecteur.objects.filter(id__in=desactives).update(actif=True)
            subprocess.run(["docker", "compose", "start", "smsc"], cwd=RACINE, capture_output=True)

        self._rendre(rapport)
        if self.fautes:
            raise CommandError(f"{len(self.fautes)} contrôle(s) en échec.")

    # Préparation ---------------------------------------------------------------

    def _note(self, texte):
        ligne = f"[{time.monotonic() - self.debut:6.1f} s] {texte}"
        self.lignes.append(ligne)
        self.stdout.write(ligne)

    def _verifier_prerequis(self):
        for nom, port in (("Jasmin", settings.JASMIN_JCLI_PORT), ("API de Jasmin", 1401)):
            try:
                socket.create_connection(("127.0.0.1", port), timeout=2).close()
            except OSError:
                raise CommandError(
                    f"{nom} injoignable : docker compose --profile passerelle up -d"
                ) from None

    def _preparer_connecteur(self):
        """Un connecteur Jasmin pour Orange, seul actif le temps du contrôle."""
        autres = list(
            Connecteur.objects.filter(operateur=Operateur.ORANGE, actif=True)
            .exclude(nom=NOM_CONNECTEUR)
            .values_list("id", flat=True)
        )
        Connecteur.objects.filter(id__in=autres).update(actif=False)
        connecteur, _ = Connecteur.objects.update_or_create(
            nom=NOM_CONNECTEUR,
            defaults={
                "operateur": Operateur.ORANGE,
                "type": Connecteur.Type.SMPP,
                "parametres": {"cid_jasmin": "simulateur"},
                "etat_liaison": Connecteur.EtatLiaison.CONNECTE,
                "actif": True,
            },
        )
        return connecteur, autres

    def _client(self):
        client = Client.objects.filter(raison_sociale="Contrôle lot 2").first()
        if client and client.palier == Client.Palier.DECOUVERTE:
            Client.objects.filter(id=client.id).update(palier=Client.Palier.VERIFIE)
            client.refresh_from_db()
        # Hors du bac à sable : le scénario envoie à des numéros quelconques.
        return client or creer_client(
            raison_sociale="Contrôle lot 2",
            forme=Client.Forme.SOCIETE,
            palier=Client.Palier.VERIFIE,
        )

    def _configurer_jasmin(self, adresse_entrants):
        self._note("Configuration de Jasmin, route des SMS entrants comprise.")
        subprocess.run(
            [
                sys.executable,
                "scripts/configurer_jasmin.py",
                "--rappels-entrants",
                adresse_entrants,
            ],
            cwd=RACINE,
            check=True,
            capture_output=True,
            timeout=180,
        )

    def _lancer(self, port, base_rappels):
        commun = {k: v for k, v in os.environ.items() if k != "SMS_CONNEXION_PAR_DEFAUT"}
        commun["JASMIN_URL_RAPPEL"] = base_rappels
        worker = commun | {"SMS_CONNEXION_PAR_DEFAUT": "WORKER"}
        # Le service web ne reçoit que le rôle applicatif : identifiants du
        # propriétaire et du worker posés vides, que le chargement de .env ne
        # remplit pas, si bien que ces connexions ne sont pas définies.
        web = commun | {
            "POSTGRES_PROPRIETAIRE_USER": "",
            "POSTGRES_PROPRIETAIRE_PASSWORD": "",
            "POSTGRES_WORKER_USER": "",
            "POSTGRES_WORKER_PASSWORD": "",
        }
        a_lancer = {
            "web": (
                [sys.executable, "manage.py", "runserver", f"0.0.0.0:{port}", "--noreload"],
                web,
            ),
            "envoi": (
                [sys.executable, "-m", "workers.envoi"],
                worker | {"SMS_FILES_ENVOI": FILE_ENVOI},
            ),
            "accuses": ([sys.executable, "-m", "workers.accuses"], worker),
            "ordonnanceur": ([sys.executable, "-m", "workers.ordonnanceur"], worker),
        }
        connexions_web = (
            subprocess.run(
                [
                    sys.executable,
                    "manage.py",
                    "shell",
                    "-c",
                    "from django.conf import settings; print(','.join(sorted(settings.DATABASES)))",
                ],
                cwd=RACINE,
                env=web,
                capture_output=True,
                text=True,
                check=True,
            )
            .stdout.strip()
            .splitlines()[-1]
        )
        if connexions_web != "default":
            raise Echec(
                f"le service web détient d'autres connexions que default : {connexions_web}"
            )
        self._note("Service web lancé avec la seule connexion du rôle applicatif.")
        for nom, (commande, env) in a_lancer.items():
            sortie = open(self.journaux / f"{nom}.log", "w", encoding="utf-8")
            self.processus[nom] = (
                subprocess.Popen(
                    commande, cwd=RACINE, env=env, stdout=sortie, stderr=subprocess.STDOUT
                ),
                sortie,
            )
        fin = time.monotonic() + 30
        while time.monotonic() < fin:
            try:
                socket.create_connection(("127.0.0.1", port), timeout=1).close()
                break
            except OSError:
                time.sleep(0.5)
        else:
            raise Echec("le service web ne répond pas")
        time.sleep(5)  # que les workers aient ouvert leurs files
        pids = ", ".join(f"{n} {p.pid}" for n, (p, _) in self.processus.items())
        self._note(f"Plateforme lancée ({pids}), journaux dans {self.journaux}.")

    def _arreter(self):
        for processus, sortie in self.processus.values():
            if processus.poll() is None:
                processus.terminate()
                try:
                    processus.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    processus.kill()
            sortie.close()

    # Scénario ------------------------------------------------------------------

    def _envoyer(self, client, nombre, lot) -> list[uuid.UUID]:
        identifiants = []
        with transaction.atomic():
            for rang in range(nombre):
                message = enregistrer_message(
                    client=client,
                    numero_normalise=f"+2267{int(time.time()) % 1000:03d}{rang:04d}",
                    operateur=Operateur.ORANGE,
                    expediteur_libelle=numero_court(),
                    texte=f"Point de contrôle du lot 2, {lot} {rang}",
                    cout_unitaire=10,
                )
                publier(
                    TOPOLOGIE.nom(FILE_ENVOI),
                    echange=TOPOLOGIE.echange,
                    objet_id=message.id,
                    donnees={
                        "message_id": str(message.id),
                        "date_reception": message.date_reception.isoformat(),
                    },
                    cle_idempotence=f"envoi:{message.id}",
                )
                identifiants.append(message.id)
        self._note(f"Lot {lot} : {nombre} messages déposés dans la file d'envoi.")
        return identifiants

    def _etats(self, identifiants) -> Counter:
        jour = timezone.now() - timezone.timedelta(days=1)
        return Counter(
            Message.objects.filter(id__in=identifiants, date_reception__gte=jour).values_list(
                "etat", flat=True
            )
        )

    def _attendre_etats_finaux(self, identifiants, delai_s, libelle):
        fin = time.monotonic() + delai_s
        while time.monotonic() < fin:
            etats = self._etats(identifiants)
            if sum(etats.values()) == len(identifiants) and set(etats) <= ETATS_TERMINAUX:
                self._note(f"{libelle} : tous à leur état final, {dict(sorted(etats.items()))}.")
                return etats
            time.sleep(1)
        etats = self._etats(identifiants)
        bloques = {e: n for e, n in etats.items() if e not in ETATS_TERMINAUX}
        raise Echec(f"{libelle} : messages en état intermédiaire après {delai_s} s : {bloques}")

    def _attendre_liaison(self, connecteur, etat, delai_s):
        debut = time.monotonic()
        while time.monotonic() - debut < delai_s:
            connecteur.refresh_from_db()
            if connecteur.etat_liaison == etat:
                return time.monotonic() - debut
            time.sleep(1)
        raise Echec(f"liaison restée {connecteur.etat_liaison}, {etat} attendu après {delai_s} s")

    def _couper_et_retablir(self, connecteur, client, pendant_coupure):
        # Le connecteur est réutilisé d'une exécution à l'autre : seules les
        # alertes émises à partir de cette coupure comptent.
        depuis = timezone.now()
        subprocess.run(
            ["docker", "compose", "stop", "smsc"], cwd=RACINE, check=True, capture_output=True
        )
        self._note("SMPPSim arrêté : coupure de la liaison.")
        delai = self._attendre_liaison(connecteur, Connecteur.EtatLiaison.DECONNECTE, 60)
        self._note(f"Coupure constatée par la surveillance en {delai:.0f} s.")

        lot_b = self._envoyer(client, pendant_coupure, "B, pendant la coupure")
        time.sleep(5)

        subprocess.run(
            ["docker", "compose", "start", "smsc"], cwd=RACINE, check=True, capture_output=True
        )
        self._note("SMPPSim relancé.")
        delai = self._attendre_liaison(connecteur, Connecteur.EtatLiaison.CONNECTE, 90)
        self._note(f"Rétablissement constaté en {delai:.0f} s.")
        self._attendre_etats_finaux(lot_b, 240, "lot B")

        alertes = list(
            EvenementSortant.objects.filter(
                objet_id=connecteur.id, nom__startswith="telecom.liaison.", cree_le__gte=depuis
            )
            .order_by("cree_le")
            .values_list("nom", flat=True)
        )
        self._note(f"Alertes de liaison : {', '.join(alertes)}.")
        # La coupure doit être suivie d'un rétablissement.
        tombee = (
            alertes.index("telecom.liaison.tombee") if "telecom.liaison.tombee" in alertes else None
        )
        if tombee is None or "telecom.liaison.retablie" not in alertes[tombee:]:
            raise Echec(f"alertes de coupure puis de rétablissement attendues, reçues : {alertes}")

    def _message_entrant(self):
        numero = f"2267{int(time.time()) % 10_000_000:07d}"
        parametres = urllib.parse.urlencode(
            {
                "short_message": "Message entrant du point de contrôle",
                "source_addr": numero,
                "destination_addr": "36123",
                "submit": "Submit Message",
                "source_addr_ton": "1",
                "source_addr_npi": "1",
                "dest_addr_ton": "1",
                "dest_addr_npi": "1",
                "esm_class": "0",
                "protocol_ID": "0",
                "priority_flag": "0",
                "registered_delivery_flag": "0",
                "data_coding": "0",
            }
        )
        port = os.environ.get("SMSC_WEB_PORT", "12775")
        fin = time.monotonic() + 60
        while True:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/inject_mo?{parametres}", timeout=5)
                break
            except OSError:
                if time.monotonic() > fin:
                    raise Echec("SMPPSim ne prend pas le message entrant") from None
                time.sleep(2)
        self._note(f"SMS entrant émis par SMPPSim depuis {numero}.")
        while time.monotonic() < fin:
            entrant = MessageEntrant.objects.filter(numero_expediteur=numero).first()
            if entrant:
                self._note(
                    f"SMS entrant en base : « {entrant.texte} », reçu à {entrant.recu_le:%H:%M:%S}."
                )
                return
            time.sleep(1)
        raise Echec("le SMS entrant n'est pas arrivé en base")

    # Rapport -------------------------------------------------------------------

    def _rendre(self, rapport):
        bilan = "RÉUSSI" if not self.fautes else "EN ÉCHEC"
        self._note(f"Point de contrôle du lot 2 {bilan}.")
        for faute in self.fautes:
            self.stdout.write(f"  FAUTE {faute}")
        if rapport:
            Path(rapport).write_text(
                json.dumps(
                    {"bilan": bilan, "etapes": self.lignes, "fautes": self.fautes}, indent=2
                ),
                encoding="utf-8",
            )
