"""Configuration versionnée de Jasmin, par sa console d'administration (jcli).

    uv run python scripts/configurer_jasmin.py [--essai]

Recrée à l'identique, à chaque passage :
- le connecteur `simulateur` vers le SMSC simulé du docker-compose ;
- le groupe `clients` et l'utilisateur de l'API HTTP de Jasmin ;
- la route par défaut des messages sortants vers ce connecteur ;
puis enregistre la configuration (persist). Rien ne se saisit à la main.

Avec --essai, envoie un message par l'API HTTP de Jasmin et attend son accusé
dans les journaux de Jasmin.
"""

import argparse
import re
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

RACINE = Path(__file__).resolve().parent.parent
INVITE = b"jcli : "
IAC, DONT, DO, WONT, WILL = 255, 254, 253, 252, 251
INVITE_SAISIE = b"> "

CONNECTEUR = {
    "cid": "simulateur",
    "host": "smsc",
    "port": "2775",
    # Compte prédéfini du simulateur. Huit caractères au plus pour le mot de
    # passe : limite du protocole SMPP.
    "username": "smppclient1",
    "password": "password",
    "submit_throughput": "50",
}


def lire_env() -> dict[str, str]:
    valeurs = {}
    for ligne in (RACINE / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in ligne and not ligne.lstrip().startswith("#"):
            nom, valeur = ligne.split("=", 1)
            valeurs[nom.strip()] = valeur.strip()
    return valeurs


class Console:
    """Client minimal de jcli : envoie une ligne, lit jusqu'à l'invite."""

    def __init__(self, hote: str, port: int, utilisateur: str, mot_de_passe: str):
        self.prise = socket.create_connection((hote, port), timeout=10)
        # La console annonce l'authentification, puis attend une ligne vide
        # avant de demander le nom d'utilisateur.
        self._lire_jusqu_a(b"Authentication required.")
        self._ecrire("")
        self._lire_jusqu_a(b"Username: ")
        self._ecrire(utilisateur)
        self._lire_jusqu_a(b"Password: ")
        self._ecrire(mot_de_passe)
        accueil = self._lire_jusqu_a(INVITE)
        if "Incorrect" in accueil:
            raise SystemExit("Authentification jcli refusée.")

    def _ecrire(self, ligne: str) -> None:
        self.prise.sendall(ligne.encode() + b"\r\n")

    def _lire_jusqu_a(self, *invites: bytes) -> str:
        """Lit jusqu'à l'une des invites, en répondant à la négociation telnet :
        refus de toute option demandée (DO), acceptation de celles proposées
        (WILL). Sans réponse, la console n'affiche jamais l'invite suivante."""
        texte = b""
        while not any(invite in texte[-len(invite) - 8 :] for invite in invites):
            morceau = self.prise.recv(4096)
            if not morceau:
                break
            position = 0
            while position < len(morceau):
                octet = morceau[position]
                if octet == IAC and position + 2 < len(morceau) + 1:
                    ordre = morceau[position + 1] if position + 1 < len(morceau) else None
                    if ordre in (DO, DONT, WILL, WONT) and position + 2 < len(morceau):
                        option = morceau[position + 2]
                        if ordre == DO:
                            self.prise.sendall(bytes([IAC, WONT, option]))
                        elif ordre == WILL:
                            self.prise.sendall(bytes([IAC, DO, option]))
                        position += 3
                        continue
                    position += 2
                    continue
                texte += bytes([octet])
                position += 1
        return texte.decode(errors="replace")

    def commande(self, ligne: str) -> str:
        self._ecrire(ligne)
        return self._lire_jusqu_a(INVITE, INVITE_SAISIE)

    def saisir(self, commande: str, champs: dict[str, str]) -> str:
        self.commande(commande)
        for cle, valeur in champs.items():
            self.commande(f"{cle} {valeur}")
        return self.commande("ok")

    def fermer(self) -> None:
        self._ecrire("quit")
        self.prise.close()


def exiger(sortie: str, attendu: str, etape: str) -> None:
    if attendu not in sortie:
        raise SystemExit(f"Échec de l'étape « {etape} » :\n{sortie.strip()}")
    print(f"  {etape} : ok")


def configurer(env: dict[str, str]) -> None:
    console = Console(
        "127.0.0.1",
        int(env["JASMIN_JCLI_PORT"]),
        env["JASMIN_JCLI_USER"],
        env["JASMIN_JCLI_PASSWORD"],
    )
    try:
        # Tout est retiré puis recréé : la configuration appliquée est toujours
        # celle de ce fichier, jamais un reste d'une saisie antérieure.
        console.commande("mtrouter -r 0")
        console.commande(f"smppccm -0 {CONNECTEUR['cid']}")
        console.commande(f"smppccm -r {CONNECTEUR['cid']}")
        console.commande(f"user -r {env['JASMIN_HTTP_USER']}")
        console.commande("group -r clients")

        exiger(console.saisir("smppccm -a", CONNECTEUR), "Successfully added", "connecteur")
        exiger(console.saisir("group -a", {"gid": "clients"}), "Successfully added", "groupe")
        utilisateur = {
            "username": env["JASMIN_HTTP_USER"],
            "password": env["JASMIN_HTTP_PASSWORD"],
            "uid": env["JASMIN_HTTP_USER"],
            "gid": "clients",
        }
        exiger(console.saisir("user -a", utilisateur), "Successfully added", "utilisateur")
        route = {"type": "defaultroute", "connector": f"smppc({CONNECTEUR['cid']})", "rate": "0.00"}
        exiger(console.saisir("mtrouter -a", route), "Successfully added", "route par défaut")
        exiger(
            console.commande(f"smppccm -1 {CONNECTEUR['cid']}"), "Successfully started", "démarrage"
        )
        exiger(console.commande("persist"), "configuration persisted", "enregistrement")
    finally:
        console.fermer()
    redemarrer(env)


def redemarrer(env: dict[str, str]) -> None:
    """Redémarre Jasmin, qui recharge la configuration enregistrée.

    Nécessaire après un retrait puis un ajout de connecteur : Jasmin 0.11 garde
    alors attaché l'ancien consommateur de la file du connecteur, qui reçoit
    les messages et les rejette (« 'NoneType' object has no attribute 'get' »).
    Un redémarrage repart d'un état propre.
    """
    subprocess.run(["docker", "compose", "restart", "jasmin"], cwd=RACINE, check=True)
    fin = time.monotonic() + 60
    while time.monotonic() < fin:
        try:
            socket.create_connection(("127.0.0.1", int(env["JASMIN_JCLI_PORT"])), timeout=2).close()
            print("  redémarrage : ok")
            time.sleep(5)  # le temps de relier la session SMPP
            return
        except OSError:
            time.sleep(1)
    raise SystemExit("Jasmin ne répond plus après son redémarrage.")


def journal_jasmin() -> str:
    return subprocess.run(
        [
            "docker",
            "compose",
            "exec",
            "-T",
            "jasmin",
            "sh",
            "-c",
            "cat /var/log/jasmin/messages.log /var/log/jasmin/dlr-thrower.log",
        ],
        capture_output=True,
        text=True,
        cwd=RACINE,
        check=True,
    ).stdout


def essayer(env: dict[str, str]) -> None:
    parametres = urllib.parse.urlencode(
        {
            "username": env["JASMIN_HTTP_USER"],
            "password": env["JASMIN_HTTP_PASSWORD"],
            "to": "22670123456",
            "from": "EXEMPLE",
            "content": "Essai de la passerelle",
            "dlr": "yes",
            "dlr-level": "3",
            "dlr-url": "http://127.0.0.1:9/accuse",
        }
    )
    adresse = f"http://127.0.0.1:{env['JASMIN_HTTP_PORT']}/send?{parametres}"
    with urllib.request.urlopen(adresse, timeout=10) as reponse:
        texte = reponse.read().decode()
    trouve = re.search(r'Success "([^"]+)"', texte)
    if not trouve:
        raise SystemExit(f"Envoi refusé par Jasmin : {texte}")
    identifiant = trouve.group(1)
    print(f"  envoi accepté, identifiant Jasmin {identifiant}")
    # Niveau 3 : Jasmin relaie deux accusés vers dlr-url, l'acceptation par le
    # SMSC puis l'état final. Le second n'existe que si Jasmin a rapproché le
    # reçu du SMSC de ce message. L'adresse de relais est volontairement
    # injoignable ici : c'est la plateforme qui la tiendra (T-23).
    fin = time.monotonic() + 30
    while time.monotonic() < fin:
        journal = journal_jasmin()
        lignes = journal.splitlines()
        relais = [ligne for ligne in lignes if f"HTTP/DLR [msgid:{identifiant}]" in ligne]
        if len(relais) >= 2:
            recus = [ligne for ligne in lignes if "DLR [cid:simulateur]" in ligne]
            print(f"  accusé final relayé par Jasmin : {recus[-1].strip()[:150]}")
            return
        time.sleep(1)
    raise SystemExit(f"Aucun accusé pour {identifiant} dans les journaux de Jasmin.")


def main() -> int:
    options = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    options.add_argument("--essai", action="store_true", help="envoie un message d'essai")
    options.add_argument(
        "--essai-seul", action="store_true", help="envoie un message d'essai sans reconfigurer"
    )
    arguments = options.parse_args()
    env = lire_env()
    if arguments.essai_seul:
        print("Essai de bout en bout :")
        essayer(env)
        return 0
    print("Configuration de Jasmin :")
    configurer(env)
    if arguments.essai:
        print("Essai de bout en bout :")
        essayer(env)
    return 0


if __name__ == "__main__":
    sys.exit(main())
