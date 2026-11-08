"""Intégration complète de l'API EL-IMMEKA SMS, depuis l'extérieur (T-114).

Ce script fait ce que fera un intégrateur, sans jamais toucher à l'interface
web : envoi par lot avec une clé d'idempotence, coupure réseau en plein envoi
puis rejeu avec la même clé, consultation du journal, réception et vérification
des webhooks, dépassement du quota de la clé. Il n'utilise que la bibliothèque
standard de Python.

Préparation, hors script : une clé d'API (de préférence d'essai, sk_test_, qui
ne coûte rien) et un point de réception déclaré vers l'adresse publique où ce
script écoute, avec son secret de signature.

    python scripts/integration_api.py --base https://sms.exemple.bf \\
        --cle sk_test_... --secret whsec_... --port 8765

Le rapport final dit pour chaque étape si elle a tenu, et le code de sortie
vaut 0 seulement si toutes ont tenu.
"""

import argparse
import hashlib
import hmac
import http.client
import json
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

TOLERANCE_S = 300


class CoupureReseau(ConnectionError):
    """La requête est partie, la réponse ne reviendra jamais."""


def transport_http(methode: str, url: str, corps: bytes | None, entetes: dict):
    """Un appel HTTP réel. Rend (statut, en-têtes, corps)."""
    requete = urllib.request.Request(url, data=corps, headers=entetes, method=methode)
    try:
        with urllib.request.urlopen(requete, timeout=30) as reponse:
            return reponse.status, dict(reponse.headers), reponse.read()
    except urllib.error.HTTPError as refus:
        return refus.code, dict(refus.headers), refus.read()


def couper_apres_envoi(methode: str, url: str, corps: bytes | None, entetes: dict):
    """Envoie la requête entière puis ferme la connexion sans lire la réponse :
    le serveur peut l'avoir traitée, le client n'en saura rien."""
    morceaux = urlsplit(url)
    classe = (
        http.client.HTTPSConnection if morceaux.scheme == "https" else http.client.HTTPConnection
    )
    connexion = classe(morceaux.netloc, timeout=30)
    chemin = morceaux.path + (f"?{morceaux.query}" if morceaux.query else "")
    connexion.request(methode, chemin, body=corps, headers=entetes)
    connexion.close()
    raise CoupureReseau(url)


class Api:
    """Client minimal : chaque appel réessaie après un 429 en suivant
    Retry-After, et compte les 429 rencontrés."""

    def __init__(self, base: str, cle: str, transport=transport_http, attendre=time.sleep):
        self.base, self.cle = base.rstrip("/"), cle
        self.transport, self.attendre = transport, attendre
        # Le transport de la coupure simulée : envoie, puis perd la réponse.
        self.transport_coupe = couper_apres_envoi
        self.refus_quota = 0

    def appeler(self, methode, chemin, corps=None, *, entetes=None, transport=None, reessayer=True):
        entetes = {"Authorization": f"Bearer {self.cle}", **(entetes or {})}
        donnees = None
        if corps is not None:
            donnees = json.dumps(corps).encode()
            entetes["Content-Type"] = "application/json"
        while True:
            statut, recus, brut = (transport or self.transport)(
                methode, self.base + chemin, donnees, entetes
            )
            recus = {cle.lower(): valeur for cle, valeur in recus.items()}
            if statut == 429:
                self.refus_quota += 1
                if reessayer:
                    self.attendre(int(recus.get("retry-after", "1")))
                    continue
            return statut, recus, json.loads(brut or b"{}")

    def envoyer(self, messages: list[dict], cle_idempotence: str, *, couper=False, essais=5):
        """Envoie un lot ; après une coupure ou un solde occupé, rejoue avec la
        même clé d'idempotence, ce qui ne produit jamais de doublon."""
        entetes = {"Idempotency-Key": cle_idempotence}
        for essai in range(essais):
            try:
                statut, recus, corps = self.appeler(
                    "POST",
                    "/v1/messages",
                    {"messages": messages},
                    entetes=entetes,
                    transport=self.transport_coupe if couper and essai == 0 else None,
                )
            except OSError:
                # Coupure, délai dépassé, connexion refusée : CoupureReseau en
                # est une. Le rejeu porte la même clé.
                self.attendre(1)
                continue
            if statut == 503:
                self.attendre(int(recus.get("retry-after", "1")))
                continue
            return statut, recus, corps
        raise RuntimeError("Envoi impossible après plusieurs essais.")

    def journal(self, debut: str, fin: str) -> list[dict]:
        lignes, apres = [], ""
        while True:
            chemin = f"/v1/messages?debut={debut}&fin={fin}&limite=200"
            statut, _, corps = self.appeler("GET", chemin + (f"&apres={apres}" if apres else ""))
            if statut != 200:
                raise RuntimeError(f"Journal illisible : {statut} {corps}")
            lignes += corps["messages"]
            if not corps["suivant"]:
                return lignes
            apres = corps["suivant"]


def signature_valide(secret: str, corps: bytes, entete: str, maintenant=None) -> bool:
    champs = dict(morceau.split("=", 1) for morceau in entete.split(",") if "=" in morceau)
    try:
        horodatage = int(champs.get("t", ""))
    except ValueError:
        return False
    if abs((maintenant or time.time()) - horodatage) > TOLERANCE_S:
        return False
    attendue = hmac.new(
        secret.encode(), f"{horodatage}.".encode() + corps, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(attendue, champs.get("v1", ""))


class Recepteur:
    """Point de réception des webhooks : vérifie chaque signature, répond 204,
    et garde chaque événement une fois, dédoublonné sur son `id`."""

    def __init__(self, secret: str, port: int = 0, hote: str = "127.0.0.1"):
        self.secret, self.evenements, self.refuses = secret, {}, 0
        recepteur = self

        class Gestionnaire(BaseHTTPRequestHandler):
            def do_POST(self):
                corps = self.rfile.read(int(self.headers.get("Content-Length", "0")))
                if not signature_valide(recepteur.secret, corps, self.headers.get("Signature", "")):
                    recepteur.refuses += 1
                    self.send_response(400)
                else:
                    evenement = json.loads(corps)
                    recepteur.evenements.setdefault(evenement["id"], evenement)
                    self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.serveur = ThreadingHTTPServer((hote, port), Gestionnaire)
        self.url = f"http://{hote}:{self.serveur.server_port}/webhooks"
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()

    def sorts(self) -> list[dict]:
        return [
            e["donnees"]
            for e in self.evenements.values()
            if e["type"] in ("client.message.livre", "client.message.echec")
        ]

    def fermer(self):
        self.serveur.shutdown()
        self.serveur.server_close()


def scenario(api: Api, recepteur: Recepteur, *, nombre: int = 20, attendre_webhooks=None):
    """Joue l'intégration et rend un rapport, une entrée par étape."""
    rapport = {}
    # Des numéros dont le dernier chiffre va de 0 à 8 : livrés ou en échec en
    # bac à sable, jamais sans accusé, pour que chacun reçoive son webhook.
    messages = [
        {"numero": f"7000{rang:04d}", "texte": "Votre colis est arrivé."}
        for rang in range(nombre * 10 // 9 + 1)
        if rang % 10 != 9
    ][:nombre]
    cle = f"integration-{uuid.uuid4()}"

    # 1. Envoi par lot, coupure réseau en plein envoi, rejeu avec la même clé.
    statut, recus, corps = api.envoyer(messages, cle, couper=True)
    ids = [m["id"] for m in corps.get("messages", []) if "id" in m]
    statut_rejeu, recus_rejeu, corps_rejeu = api.envoyer(messages, cle)
    rapport["envoi"] = statut == 202 and len(ids) == nombre
    rapport["rejeu_sans_doublon"] = (
        statut_rejeu == 202
        and recus_rejeu.get("idempotent-rejoue") == "true"
        and [m.get("id") for m in corps_rejeu["messages"]]
        == [m.get("id") for m in corps["messages"]]
    )

    # 2. Journal : chaque message une fois, aucun de plus.
    aujourd_hui = date.today().isoformat()
    vus = [ligne["id"] for ligne in api.journal(aujourd_hui, aujourd_hui) if ligne["id"] in ids]
    rapport["journal"] = sorted(vus) == sorted(ids)

    # 3. Webhooks : un sort final par message envoyé, un pour un.
    if attendre_webhooks:
        attendre_webhooks()
    else:
        limite = time.monotonic() + 120
        while len(recepteur.sorts()) < len(ids) and time.monotonic() < limite:
            time.sleep(1)
    recus_ids = [sort["message_id"] for sort in recepteur.sorts()]
    rapport["webhooks_un_pour_un"] = sorted(recus_ids) == sorted(ids) and not recepteur.refuses

    # 4. Quota de la clé : des appels en rafale jusqu'au 429, puis l'attente
    # indiquée, après laquelle l'appel suivant passe.
    attente = None
    for _ in range(1000):
        statut, recus, _ = api.appeler("GET", "/v1/solde", reessayer=False)
        if statut == 429:
            attente = int(recus.get("retry-after", "1"))
            break
    if attente is not None:
        api.attendre(attente)
        statut, _, _ = api.appeler("GET", "/v1/solde", reessayer=False)
    rapport["quota"] = attente is not None and statut == 200
    return rapport


def principal() -> int:
    parametres = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parametres.add_argument("--base", required=True, help="Adresse de la plateforme.")
    parametres.add_argument("--cle", required=True, help="Clé d'API, sk_test_ de préférence.")
    parametres.add_argument("--secret", required=True, help="Secret du point de réception.")
    parametres.add_argument("--port", type=int, default=8765, help="Port d'écoute des webhooks.")
    parametres.add_argument("--hote", default="0.0.0.0", help="Adresse d'écoute des webhooks.")
    parametres.add_argument("--nombre", type=int, default=20, help="Messages du lot.")
    arguments = parametres.parse_args()
    recepteur = Recepteur(arguments.secret, arguments.port, arguments.hote)
    try:
        rapport = scenario(Api(arguments.base, arguments.cle), recepteur, nombre=arguments.nombre)
    finally:
        recepteur.fermer()
    for etape, tenue in rapport.items():
        print(f"{'tenu ' if tenue else 'ÉCHEC'}  {etape}")
    return 0 if all(rapport.values()) else 1


if __name__ == "__main__":
    sys.exit(principal())
