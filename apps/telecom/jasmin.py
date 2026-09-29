"""Adaptateur Jasmin : le contrat des passerelles au-dessus de l'API HTTP de Jasmin.

Jasmin tient les sessions SMPP, les files et le lissage de débit ; la
plateforme choisit la route et garde le coût. L'identifiant rendu par Jasmin à
la soumission est celui que portent ensuite ses rappels d'accusé : c'est lui
que la corrélation retient.

Jasmin pousse ses accusés vers `JASMIN_URL_RAPPEL` (niveau 3 : l'acceptation
par le SMSC, puis l'état final). Seul l'état final devient un accusé interne.
"""

import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import ClassVar

from django.conf import settings
from django.utils import timezone

from apps.core.encodage import Encodage, analyser
from apps.core.etats import Etat

from .jcli import Console, etat_des_sessions
from .passerelles import (
    Accuse,
    EtatLiaison,
    Passerelle,
    PasserelleIndisponible,
    SortSoumission,
    Soumission,
    SoumissionRejetee,
    Statut,
)
from .rappels import jeton

journal = logging.getLogger(__name__)

REPONSE_ACCEPTEE = re.compile(r'Success "([^"]+)"')
REPONSE_REFUSEE = re.compile(r'Error "([^"]*)"')
DELAI_S = 10

# Réponse attendue par Jasmin à un rappel : sans elle, il le relaie à nouveau.
ACQUITTEMENT_RAPPEL = "ACK/Jasmin"


def adresse_rappel(base: str, connecteur_id) -> str:
    """Adresse où Jasmin relaie accusés et SMS entrants d'un connecteur."""
    return f"{base.rstrip('/')}/{connecteur_id}?jeton={jeton(connecteur_id)}"


class Jasmin(Passerelle):
    # L'API HTTP de Jasmin ne dit pas si elle a reçu une soumission dont on n'a
    # pas la référence : après une coupure, le sort reste inconnu.
    retrouve_ses_soumissions: ClassVar[bool] = False
    correspondance: ClassVar[dict[str, Etat]] = {
        "DELIVRD": Etat.LIVRE,
        "UNDELIV": Etat.NON_LIVRE,
        "EXPIRED": Etat.EXPIRE,
        "REJECTD": Etat.REJETE,
        "DELETED": Etat.NON_LIVRE,
        "UNKNOWN": Etat.NON_LIVRE,
    }

    def __init__(self, connecteur, url: str | None = None, url_rappel: str | None = None):
        super().__init__(connecteur)
        self.url = (url or settings.JASMIN_HTTP_URL).rstrip("/")
        self.url_rappel = url_rappel if url_rappel is not None else settings.JASMIN_URL_RAPPEL
        self._etat = EtatLiaison.CONNECTE

    def etat(self) -> EtatLiaison:
        return self._etat

    def _parametres(self, soumission: Soumission) -> dict[str, str]:
        parametres = {
            "username": settings.JASMIN_HTTP_USER,
            "password": settings.JASMIN_HTTP_PASSWORD,
            "to": soumission.numero.removeprefix("+"),
            "from": soumission.expediteur,
            # Pas de validity-period : Jasmin 0.11 échoue parfois à le convertir en
            # date SMPP (« tenths of second must be one digit ») et rejette alors
            # le message après l'avoir accepté, sans accusé. La validité par
            # défaut du SMSC s'applique ; VALIDITE_SMS_HEURES doit la refléter.
        }
        if analyser(soumission.texte).encodage == Encodage.UCS2:
            parametres |= {"coding": "8", "hex-content": soumission.texte.encode("utf-16-be").hex()}
        else:
            parametres["content"] = soumission.texte
        if self.url_rappel:
            parametres |= {
                "dlr": "yes",
                "dlr-level": "3",
                "dlr-method": "POST",
                "dlr-url": adresse_rappel(self.url_rappel, self.connecteur.id),
            }
        return parametres

    def soumettre(self, soumission: Soumission) -> str:
        self._exiger_disponible()
        corps = urllib.parse.urlencode(self._parametres(soumission)).encode()
        requete = urllib.request.Request(f"{self.url}/send", data=corps, method="POST")
        try:
            with urllib.request.urlopen(requete, timeout=DELAI_S) as reponse:
                texte = reponse.read().decode(errors="replace")
        except urllib.error.HTTPError as erreur:
            texte = erreur.read().decode(errors="replace")
            motif = REPONSE_REFUSEE.search(texte)
            raise SoumissionRejetee(str(erreur.code), motif.group(1) if motif else texte) from None
        except OSError as erreur:
            self._etat = EtatLiaison.DECONNECTE
            raise PasserelleIndisponible(f"API de Jasmin injoignable : {erreur}") from None
        self._etat = EtatLiaison.CONNECTE
        accepte = REPONSE_ACCEPTEE.search(texte)
        if not accepte:
            raise SoumissionRejetee("reponse", texte.strip()[:200])
        return accepte.group(1)

    def sonder(self) -> EtatLiaison:
        """État de la session SMPP de ce connecteur dans Jasmin, lu sur sa
        console d'administration. Console injoignable : liaison tombée."""
        cid = (self.connecteur.parametres or {}).get("cid_jasmin", "simulateur")
        try:
            console = Console(
                settings.JASMIN_JCLI_HOST,
                settings.JASMIN_JCLI_PORT,
                settings.JASMIN_JCLI_USER,
                settings.JASMIN_JCLI_PASSWORD,
            )
            try:
                session = etat_des_sessions(console).get(cid, "NONE")
            finally:
                console.fermer()
        except (OSError, PermissionError) as erreur:
            journal.warning("Console de Jasmin injoignable : %s", erreur)
            self._etat = EtatLiaison.DECONNECTE
            return self._etat
        self._etat = EtatLiaison.CONNECTE if session.startswith("BOUND") else EtatLiaison.DECONNECTE
        return self._etat

    def statut(self, soumission: Soumission, reference_smsc: str | None = None) -> Statut:
        # L'API HTTP de Jasmin ne sait pas dire si elle a reçu une soumission :
        # une référence connue vaut acceptation, son absence vaut inconnue.
        if reference_smsc:
            return Statut(SortSoumission.ACCEPTEE, reference_smsc)
        return Statut(SortSoumission.INCONNUE)

    def accuses_dus(self, maintenant: datetime | None = None) -> list[Accuse]:
        return []  # Jasmin pousse ses accusés : voir interpreter_rappel

    def interpreter_rappel(self, donnees: dict) -> Accuse | None:
        """Un rappel d'accusé de Jasmin, champs `id`, `message_status`, `level`.

        Le niveau 1 annonce l'acceptation par le SMSC (ESME_ROK) : pas un état
        final, ignoré. Le niveau 2 porte l'état final.
        """
        if str(donnees.get("level")) != "2":
            return None
        libelle = str(donnees.get("message_status", ""))
        return Accuse(
            connecteur_id=self.connecteur.id,
            reference_smsc=str(donnees["id"]),
            libelle_operateur=libelle,
            etat=self.traduire(libelle),
            horodatage=timezone.now(),
        )
