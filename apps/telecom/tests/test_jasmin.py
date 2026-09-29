"""Adaptateur Jasmin, T-23.

Les tests unitaires tournent contre une fausse API HTTP de Jasmin, partout, y
compris dans la chaîne. Le test de bascule tourne aussi contre le vrai Jasmin
du docker-compose quand il est lancé (profil passerelle), et s'efface sinon.
"""

import socket
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from django.conf import settings
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.etats import ETATS_TERMINAUX, Etat
from apps.journal.models import Message
from apps.journal.services import appliquer_accuse, enregistrer_message, soumettre
from apps.telecom import correlation
from apps.telecom.jasmin import ACQUITTEMENT_RAPPEL, Jasmin
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import (
    PasserelleIndisponible,
    Soumission,
    SoumissionRejetee,
    passerelle_pour,
)


class _Serveur:
    """Petit serveur HTTP dans un fil : fausse API de Jasmin ou réception des rappels."""

    def __init__(self, repondre, hote="127.0.0.1"):
        self.requetes = []
        serveur = self

        class Gestionnaire(BaseHTTPRequestHandler):
            def do_POST(self):
                longueur = int(self.headers.get("Content-Length", 0))
                corps = urllib.parse.parse_qs(self.rfile.read(longueur).decode())
                donnees = {cle: valeurs[0] for cle, valeurs in corps.items()}
                serveur.requetes.append((self.path, donnees))
                code, texte = repondre(self.path, donnees)
                self.send_response(code)
                self.end_headers()
                self.wfile.write(texte.encode())

            def log_message(self, *args):
                pass

        self.http = ThreadingHTTPServer((hote, 0), Gestionnaire)
        self.port = self.http.server_address[1]
        threading.Thread(target=self.http.serve_forever, daemon=True).start()

    def fermer(self):
        self.http.shutdown()
        self.http.server_close()


@pytest.fixture
def fausse_api():
    serveur = _Serveur(lambda chemin, donnees: (200, f'Success "{uuid.uuid4()}"'))
    yield serveur
    serveur.fermer()


def _jasmin(url, url_rappel="http://plateforme/rappels/jasmin"):
    connecteur = Connecteur(
        id=uuid.uuid7(), nom="jasmin", operateur=Operateur.ORANGE, type=Connecteur.Type.SMPP
    )
    return Jasmin(connecteur, url=url, url_rappel=url_rappel)


def test_une_soumission_gsm_part_en_texte_avec_demande_d_accuse(fausse_api, settings):
    settings.JASMIN_HTTP_USER, settings.JASMIN_HTTP_PASSWORD = "sms", "secret"
    passerelle = _jasmin(f"http://127.0.0.1:{fausse_api.port}")
    reference = passerelle.soumettre(Soumission("+22670123456", "Bonjour à vous", "EXEMPLE"))

    chemin, envoye = fausse_api.requetes[0]
    assert chemin == "/send" and uuid.UUID(reference)
    assert envoye["to"] == "22670123456" and envoye["from"] == "EXEMPLE"
    assert envoye["content"] == "Bonjour à vous" and "coding" not in envoye
    assert (envoye["username"], envoye["password"]) == ("sms", "secret")
    assert envoye["dlr"] == "yes" and envoye["dlr-level"] == "3"
    assert envoye["dlr-url"] == f"http://plateforme/rappels/jasmin/{passerelle.connecteur.id}"


def test_une_soumission_ucs2_part_en_hexadecimal_code_8(fausse_api):
    passerelle = _jasmin(f"http://127.0.0.1:{fausse_api.port}")
    passerelle.soumettre(Soumission("+22670123456", "Tête-à-tête", "EXEMPLE"))
    envoye = fausse_api.requetes[0][1]
    assert envoye["coding"] == "8"
    assert bytes.fromhex(envoye["hex-content"]).decode("utf-16-be") == "Tête-à-tête"


def test_un_refus_de_jasmin_devient_un_rejet_avec_son_motif():
    serveur = _Serveur(
        lambda chemin, donnees: (400, 'Error "Mandatory argument [to] is not found."')
    )
    try:
        with pytest.raises(SoumissionRejetee, match="Mandatory argument"):
            _jasmin(f"http://127.0.0.1:{serveur.port}").soumettre(
                Soumission("+22670123456", "x", "E")
            )
    finally:
        serveur.fermer()


def test_une_api_injoignable_rend_la_passerelle_indisponible():
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        port_libre = prise.getsockname()[1]
    passerelle = _jasmin(f"http://127.0.0.1:{port_libre}")
    with pytest.raises(PasserelleIndisponible):
        passerelle.soumettre(Soumission("+22670123456", "x", "E"))
    assert not passerelle.disponible


def test_un_rappel_de_niveau_1_n_est_pas_un_accuse_final():
    passerelle = _jasmin("http://inutile")
    assert (
        passerelle.interpreter_rappel({"id": "abc", "level": "1", "message_status": "ESME_ROK"})
        is None
    )


@pytest.mark.parametrize(
    ("statut", "etat"),
    [
        ("DELIVRD", Etat.LIVRE),
        ("UNDELIV", Etat.NON_LIVRE),
        ("EXPIRED", Etat.EXPIRE),
        ("REJECTD", Etat.REJETE),
        ("INCONNU", Etat.NON_LIVRE),
    ],
)
def test_un_rappel_de_niveau_2_devient_un_accuse_interne(statut, etat):
    passerelle = _jasmin("http://inutile")
    accuse = passerelle.interpreter_rappel({"id": "ref-9", "level": "2", "message_status": statut})
    assert (accuse.reference_smsc, accuse.etat, accuse.connecteur_id) == (
        "ref-9",
        etat,
        passerelle.connecteur.id,
    )


def _jasmin_lance() -> bool:
    try:
        port = int(settings.JASMIN_HTTP_URL.rsplit(":", 1)[1])
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return bool(settings.JASMIN_HTTP_USER)
    except OSError, ValueError, IndexError:
        return False


def _scenario(connecteur, relever_accuses) -> Message:
    """Le même scénario applicatif, quel que soit l'adaptateur : écrire, soumettre,
    appliquer l'accusé. Aucun appel ne nomme l'adaptateur."""
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    message = enregistrer_message(
        client=client,
        numero_normalise="+22670123456",
        operateur=connecteur.operateur,
        expediteur_libelle="EXEMPLE",
        texte="Essai de bascule",
        cout_unitaire=10,
    )
    passerelle = passerelle_pour(connecteur)
    message = soumettre(message, passerelle)
    for accuse in relever_accuses(passerelle):
        appliquer_accuse(accuse)
    correlation._redis().delete(correlation.cle_redis(connecteur.id, message.reference_smsc))
    return Message.objects.get(id=message.id)


@pytest.mark.django_db(transaction=True)
def test_la_bascule_par_configuration_vers_le_simulateur(en_tant_que_worker):
    connecteur = Connecteur.objects.create(
        nom=f"simulateur-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SIMULE,
        parametres={"taux_echec": 0},
    )
    message = _scenario(connecteur, lambda passerelle: passerelle.accuses_dus())
    assert message.etat == Etat.LIVRE


@pytest.mark.skipif(not _jasmin_lance(), reason="Jasmin n'est pas lancé (profil passerelle)")
@pytest.mark.django_db(transaction=True)
def test_la_bascule_par_configuration_vers_jasmin(en_tant_que_worker, settings):
    rappels = _Serveur(lambda chemin, donnees: (200, ACQUITTEMENT_RAPPEL), hote="0.0.0.0")
    settings.JASMIN_URL_RAPPEL = f"http://host.docker.internal:{rappels.port}/rappels/jasmin"
    connecteur = Connecteur.objects.create(
        nom=f"jasmin-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SMPP,
    )

    def relever_par_rappel(passerelle):
        fin = time.monotonic() + 40
        while time.monotonic() < fin:
            for _, donnees in list(rappels.requetes):
                accuse = passerelle.interpreter_rappel(donnees)
                if accuse is not None:
                    return [accuse]
            time.sleep(0.5)
        raise AssertionError(f"aucun accusé final relayé par Jasmin : {rappels.requetes}")

    try:
        message = _scenario(connecteur, relever_par_rappel)
    finally:
        rappels.fermer()
    assert message.etat in ETATS_TERMINAUX
    assert message.reference_smsc and uuid.UUID(message.reference_smsc)
    chemins = {chemin for chemin, _ in rappels.requetes}
    assert chemins == {f"/rappels/jasmin/{connecteur.id}"}
