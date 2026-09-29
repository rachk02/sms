"""Point d'entrée des rappels et chemin des SMS entrants, T-28."""

import asyncio
import uuid

import pytest
from asgiref.sync import sync_to_async
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core import bus
from apps.core.etats import Etat
from apps.core.models import EvenementSortant
from apps.journal.models import Message
from apps.journal.services import enregistrer_message, soumettre
from apps.telecom import correlation
from apps.telecom.jasmin import ACQUITTEMENT_RAPPEL
from apps.telecom.models import Connecteur, MessageEntrant, Operateur
from apps.telecom.passerelles import passerelle_pour
from apps.telecom.rappels import jeton
from workers.accuses import WorkerAccuses

CONNECTEUR = uuid.uuid7()


def _adresse(connecteur_id=CONNECTEUR, avec_jeton=True):
    base = f"/rappels/jasmin/{connecteur_id}"
    return f"{base}?jeton={jeton(connecteur_id)}" if avec_jeton else base


def _sortants(cle_commence):
    return EvenementSortant.objects.using("worker").filter(cle_idempotence__startswith=cle_commence)


ACCUSE_FINAL = {"id": "ref-1", "level": "2", "message_status": "DELIVRD"}
ENTRANT = {"id": "mo-1", "from": "22670123456", "to": "36123", "content": "STOP"}


@pytest.mark.django_db(databases=["default", "worker"])
@pytest.mark.parametrize(
    "adresse", [_adresse(avec_jeton=False), f"{_adresse(avec_jeton=False)}?jeton=faux"]
)
def test_un_rappel_sans_le_bon_jeton_est_refuse(client, adresse):
    assert client.post(adresse, ACCUSE_FINAL).status_code == 403


@pytest.mark.django_db
def test_un_jeton_d_un_autre_connecteur_est_refuse(client):
    autre = uuid.uuid7()
    adresse = f"/rappels/jasmin/{CONNECTEUR}?jeton={jeton(autre)}"
    assert client.post(adresse, ACCUSE_FINAL).status_code == 403


@pytest.mark.django_db
def test_seul_post_est_accepte(client):
    assert client.get(_adresse()).status_code == 405


@pytest.mark.django_db(transaction=True, databases=["default", "worker"])
def test_accuse_et_entrant_par_la_meme_adresse_partent_chacun_dans_sa_file(client):
    reponse = client.post(_adresse(), ACCUSE_FINAL)
    assert (reponse.status_code, reponse.content.decode()) == (200, ACQUITTEMENT_RAPPEL)
    assert client.post(_adresse(), ENTRANT).status_code == 200

    accuse = _sortants(f"accuse:{CONNECTEUR}:ref-1").get()
    entrant = _sortants(f"entrant:{CONNECTEUR}:mo-1").get()
    assert (accuse.echange, accuse.nom) == ("sms", "accuses.entrants")
    assert (entrant.echange, entrant.nom) == ("sms", "messages.entrants")
    assert accuse.donnees["etat"] == Etat.LIVRE and accuse.donnees["reference_smsc"] == "ref-1"
    assert (
        entrant.donnees["texte"] == "STOP" and entrant.donnees["numero_expediteur"] == "22670123456"
    )


@pytest.mark.django_db(transaction=True, databases=["default", "worker"])
def test_un_accuse_de_niveau_1_est_acquitte_sans_rien_deposer(client):
    rappel = {"id": "ref-2", "level": "1", "message_status": "ESME_ROK"}
    assert client.post(_adresse(), rappel).status_code == 200
    assert not _sortants(f"accuse:{CONNECTEUR}:ref-2").exists()


@pytest.mark.django_db(transaction=True, databases=["default", "worker"])
def test_un_rappel_rejoue_ne_depose_qu_une_fois(client):
    for _ in range(3):
        client.post(_adresse(), ENTRANT)
    assert _sortants(f"entrant:{CONNECTEUR}:mo-1").count() == 1


@pytest.mark.django_db
def test_un_rappel_ni_accuse_ni_entrant_est_refuse(client):
    assert client.post(_adresse(), {"id": "x"}).status_code == 400


@pytest.mark.django_db(transaction=True, databases=["default", "worker"])
def test_du_rappel_a_la_base_accuse_applique_et_entrant_ecrit(client, en_tant_que_worker):
    connecteur = Connecteur.objects.create(
        nom=f"c-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SIMULE,
    )
    message = enregistrer_message(
        client=creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE),
        numero_normalise="+22670123456",
        operateur="ORANGE",
        expediteur_libelle="EXEMPLE",
        texte="Bonjour",
        cout_unitaire=10,
    )
    message = soumettre(message, passerelle_pour(connecteur))

    adresse = _adresse(connecteur.id)
    client.post(adresse, {"id": message.reference_smsc, "level": "2", "message_status": "DELIVRD"})
    client.post(adresse, {"id": "mo-9", "from": "22670999999", "to": "36123", "content": "Aide"})

    worker = WorkerAccuses()

    async def scenario():
        connexion = await bus.connecter()
        async with connexion:
            await bus.relayer(await connexion.channel())
        execution = asyncio.create_task(worker.executer(installer_signaux=False))
        await asyncio.wait_for(worker.pret.wait(), 10)
        for _ in range(50):
            fini = await sync_to_async(
                lambda: (
                    Message.objects.get(id=message.id).etat == Etat.LIVRE
                    and MessageEntrant.objects.filter(reference="mo-9").exists()
                )
            )()
            if fini:
                break
            await asyncio.sleep(0.2)
        worker.arret.set()
        await asyncio.wait_for(execution, 15)

    try:
        asyncio.run(scenario())
    finally:
        correlation._redis().delete(correlation.cle_redis(connecteur.id, message.reference_smsc))

    assert Message.objects.get(id=message.id).etat == Etat.LIVRE
    entrant = MessageEntrant.objects.get(reference="mo-9")
    assert (entrant.numero_expediteur, entrant.texte, entrant.connecteur_id) == (
        "22670999999",
        "Aide",
        connecteur.id,
    )
    assert entrant.recu_le is not None and entrant.client_id is None


def _jasmin_lance() -> bool:
    import socket

    from django.conf import settings

    try:
        port = int(settings.JASMIN_HTTP_URL.rsplit(":", 1)[1])
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return bool(settings.JASMIN_HTTP_USER)
    except OSError, ValueError, IndexError:
        return False


@pytest.mark.skipif(not _jasmin_lance(), reason="Jasmin n'est pas lancé (profil passerelle)")
@pytest.mark.django_db(transaction=True, databases=["default", "worker"])
def test_un_sms_emis_par_le_simulateur_atterrit_en_base(en_tant_que_worker):
    """Critère de T-28 : SMPPSim émet un SMS entrant, Jasmin le relaie au point
    d'entrée de rappel, le worker l'écrit avec son numéro, son texte et son heure."""
    import os
    import subprocess
    import sys
    import threading
    import time
    import urllib.parse
    import urllib.request
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    from django.db import connections
    from django.test import Client as ClientHttp

    connecteur = Connecteur.objects.create(
        nom=f"jasmin-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SMPP,
    )
    recus = []

    class Relais(BaseHTTPRequestHandler):
        """Reçoit l'appel de Jasmin et le rejoue sur le vrai point d'entrée."""

        def do_POST(self):
            longueur = int(self.headers.get("Content-Length", 0))
            donnees = urllib.parse.parse_qs(self.rfile.read(longueur).decode(errors="replace"))
            donnees = {cle: valeurs[0] for cle, valeurs in donnees.items()}
            reponse = ClientHttp().post(self.path, donnees)
            connections.close_all()
            recus.append((reponse.status_code, donnees))
            self.send_response(reponse.status_code)
            self.end_headers()
            self.wfile.write(reponse.content)

        def log_message(self, *args):
            pass

    serveur = ThreadingHTTPServer(("0.0.0.0", 0), Relais)
    threading.Thread(target=serveur.serve_forever, daemon=True).start()
    adresse = (
        f"http://host.docker.internal:{serveur.server_address[1]}"
        f"/rappels/jasmin/{connecteur.id}?jeton={jeton(connecteur.id)}"
    )
    racine = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    try:
        subprocess.run(
            [sys.executable, "scripts/configurer_jasmin.py", "--rappels-entrants", adresse],
            cwd=racine,
            check=True,
            capture_output=True,
            timeout=180,
        )
        numero = f"2267{int(time.time()) % 10_000_000:07d}"
        parametres = urllib.parse.urlencode(
            {
                "short_message": "Bonjour plateforme",
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
        port_web = os.environ.get("SMSC_WEB_PORT", "12775")
        urllib.request.urlopen(f"http://127.0.0.1:{port_web}/inject_mo?{parametres}", timeout=10)

        fin = time.monotonic() + 30
        while not recus and time.monotonic() < fin:
            time.sleep(0.5)
        assert recus and recus[0][0] == 200, recus

        worker = WorkerAccuses()

        async def scenario():
            connexion = await bus.connecter()
            async with connexion:
                await bus.relayer(await connexion.channel())
            execution = asyncio.create_task(worker.executer(installer_signaux=False))
            await asyncio.wait_for(worker.pret.wait(), 10)
            for _ in range(50):
                trouve = await sync_to_async(
                    MessageEntrant.objects.filter(numero_expediteur=numero).exists
                )()
                if trouve:
                    break
                await asyncio.sleep(0.2)
            worker.arret.set()
            await asyncio.wait_for(execution, 15)

        asyncio.run(scenario())
    finally:
        serveur.shutdown()
        serveur.server_close()

    entrant = MessageEntrant.objects.get(numero_expediteur=numero)
    assert (entrant.texte, entrant.numero_destination) == ("Bonjour plateforme", "36123")
    assert abs((timezone.now() - entrant.recu_le).total_seconds()) < 120
