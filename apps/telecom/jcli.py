"""Client minimal de jcli, la console d'administration de Jasmin.

Utilisé par le script de configuration (scripts/configurer_jasmin.py) et par
l'adaptateur Jasmin pour constater l'état des sessions SMPP. La console parle
telnet : elle négocie des options à la connexion et attend qu'on lui réponde.
"""

import socket

INVITE = b"jcli : "
INVITE_SAISIE = b"> "
IAC, DONT, DO, WONT, WILL = 255, 254, 253, 252, 251


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
            raise PermissionError("Authentification jcli refusée.")

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


def etat_des_sessions(console: Console) -> dict[str, str]:
    """Session de chaque connecteur SMPP de Jasmin : BOUND_TRX, NONE, BINDING..."""
    sessions = {}
    for ligne in console.commande("smppccm --list").splitlines():
        champs = ligne.lstrip("#").split()
        if len(champs) >= 3 and champs[0] not in {"Connector", "Total"}:
            sessions[champs[0]] = champs[2]
    return sessions
