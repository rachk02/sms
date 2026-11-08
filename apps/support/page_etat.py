"""Page publique d'état du service (T-103).

Sept composants, quatre états. Chaque minute, l'ordonnanceur construit l'état
et le pousse, avec la page qui l'affiche, vers un hébergement statique séparé
de la plateforme : si elle ne répond plus, la page continue d'afficher le
dernier état connu, et dit depuis quand il n'a pas été rafraîchi.

Le passage en dégradé est automatique, sur les mêmes signaux que la
supervision : une liaison d'opérateur qui n'est pas saine, un worker sans
battement. Le passage en panne, et toute communication, passent par un incident
déclaré par un humain (`services.declarer_incident`).

L'hébergement n'est pas choisi (décision du lot 8) : le dépôt se fait derrière
une abstraction, `Depot`, réglée par `settings.PAGE_ETAT`. Deux dépôts existent :
un dossier local, et un seau compatible S3.
"""

import io
import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .models import Incident

Composant = Incident.Composant

OPERATIONNEL = "OPERATIONNEL"
LIBELLES_ETATS = {
    OPERATIONNEL: "Opérationnel",
    Incident.Etat.DEGRADE: "Dégradé",
    Incident.Etat.PANNE: "En panne",
    Incident.Etat.MAINTENANCE: "Maintenance planifiée",
}
# Du moins grave au plus grave : l'état affiché est le plus grave des signaux.
GRAVITE = [OPERATIONNEL, Incident.Etat.MAINTENANCE, Incident.Etat.DEGRADE, Incident.Etat.PANNE]
HISTORIQUE = timedelta(days=90)
# Les workers dont l'absence dégrade un composant.
WORKERS = {Composant.ACCUSES: "accuses", Composant.RECHARGEMENT: "paiements"}
PAGE = Path(__file__).resolve().parent / "page_etat"
SCRIPT = Path(settings.BASE_DIR) / "static" / "js" / "page_etat.js"


def _plus_grave(*etats) -> str:
    return max(etats, key=GRAVITE.index)


def etats_automatiques() -> dict:
    """Les signaux de la supervision, lus sous le rôle worker : jamais une panne,
    au plus un dégradé."""
    from apps.core import battements
    from apps.telecom.models import Connecteur

    etats = {code: OPERATIONNEL for code in Composant.values}
    for operateur in (Composant.ORANGE, Composant.MOOV, Composant.TELECEL):
        liaisons = list(
            Connecteur.objects.filter(operateur=operateur, actif=True).values_list(
                "etat_liaison", flat=True
            )
        )
        # Aucune liaison active, ou une liaison qui n'est pas saine : dégradé.
        if not liaisons or any(e != Connecteur.EtatLiaison.CONNECTE for e in liaisons):
            etats[operateur] = Incident.Etat.DEGRADE
    vivants = {b["nom"] for b in battements.lire()}
    for composant, worker in WORKERS.items():
        if worker not in vivants:
            etats[composant] = Incident.Etat.DEGRADE
    return etats


def _instant(valeur) -> str:
    return valeur.isoformat() if valeur else ""


def _incident(incident: Incident) -> dict:
    return {
        "titre": incident.titre,
        "etat": incident.etat,
        "libelle_etat": incident.get_etat_display(),
        "composants": [Composant(c).label for c in incident.composants],
        "debut_le": _instant(incident.debut_le),
        "fin_le": _instant(incident.fin_le),
        "publications": [
            {"texte": p.texte, "etat": p.etat, "publiee_le": _instant(p.publiee_le)}
            for p in incident.publications.all()
        ],
    }


def construire(maintenant=None, automatiques: dict | None = None) -> dict:
    """L'état publié : composants, incidents en cours, maintenances prévues et
    quatre-vingt-dix jours d'historique."""
    maintenant = maintenant or timezone.now()
    automatiques = etats_automatiques() if automatiques is None else automatiques
    incidents = list(
        Incident.objects.filter(Q(fin_le__isnull=True) | Q(fin_le__gte=maintenant - HISTORIQUE))
        .prefetch_related("publications")
        .order_by("-debut_le")
    )
    en_cours = [
        i
        for i in incidents
        if i.fin_le is None and i.etat != Incident.Etat.RETABLI and i.debut_le <= maintenant
    ]
    prevues = [i for i in incidents if i.fin_le is None and i.debut_le > maintenant]
    termines = [i for i in incidents if i.fin_le is not None]
    composants = []
    for code in Composant.values:
        declares = [i.etat for i in en_cours if code in i.composants]
        etat = _plus_grave(automatiques[code], *declares)
        composants.append(
            {
                "code": code,
                "libelle": Composant(code).label,
                "etat": etat,
                "libelle_etat": LIBELLES_ETATS[etat],
                "source": "incident" if declares and etat in declares else "supervision",
            }
        )
    return {
        "version": 1,
        "genere_le": maintenant.isoformat(timespec="seconds"),
        "composants": composants,
        "en_cours": [_incident(i) for i in en_cours],
        "prevues": [_incident(i) for i in prevues],
        "historique": [_incident(i) for i in termines],
    }


def page() -> str:
    """La page, autonome : son script est inclus, elle ne lit que le fichier
    d'état posé à côté d'elle, jamais la plateforme."""
    gabarit = (PAGE / "index.html").read_text(encoding="utf-8")
    return gabarit.replace("/* SCRIPT */", SCRIPT.read_text(encoding="utf-8"))


# Dépôts


class Depot:
    def deposer(self, nom: str, contenu: bytes, type_mime: str) -> None:
        raise NotImplementedError

    def lire(self, nom: str) -> bytes | None:
        raise NotImplementedError


class DepotDossier(Depot):
    """Un dossier local, servi par n'importe quel serveur statique. L'écriture
    passe par un fichier temporaire renommé : la page ne lit jamais un état à
    moitié écrit."""

    def __init__(self, dossier):
        self.dossier = Path(dossier)

    def deposer(self, nom, contenu, type_mime):
        self.dossier.mkdir(parents=True, exist_ok=True)
        provisoire = self.dossier / f".{nom}.part"
        provisoire.write_bytes(contenu)
        provisoire.replace(self.dossier / nom)

    def lire(self, nom):
        chemin = self.dossier / nom
        return chemin.read_bytes() if chemin.exists() else None


class DepotSeau(Depot):
    """Un seau compatible S3, chez l'hébergeur qui sera retenu."""

    def __init__(self, point_acces, identifiant, secret, seau):
        from urllib.parse import urlparse

        from minio import Minio

        adresse = urlparse(point_acces)
        self.client = Minio(
            adresse.netloc,
            access_key=identifiant,
            secret_key=secret,
            secure=adresse.scheme == "https",
        )
        self.seau = seau

    def deposer(self, nom, contenu, type_mime):
        if not self.client.bucket_exists(self.seau):
            self.client.make_bucket(self.seau)
        self.client.put_object(
            self.seau,
            nom,
            io.BytesIO(contenu),
            len(contenu),
            content_type=type_mime,
            metadata={"Cache-Control": "no-cache"},
        )

    def lire(self, nom):
        from minio.error import S3Error

        try:
            reponse = self.client.get_object(self.seau, nom)
        except S3Error:
            return None
        try:
            return reponse.read()
        finally:
            reponse.close()
            reponse.release_conn()


def depot() -> Depot:
    reglage = settings.PAGE_ETAT
    if reglage["depot"] == "seau":
        return DepotSeau(
            reglage["point_acces"], reglage["identifiant"], reglage["secret"], reglage["seau"]
        )
    return DepotDossier(reglage["dossier"])


def publier(maintenant=None, cible: Depot | None = None) -> dict:
    """Construit l'état et le pousse avec la page. Appelée chaque minute par
    l'ordonnanceur."""
    cible = cible or depot()
    etat = construire(maintenant)
    cible.deposer("index.html", page().encode("utf-8"), "text/html; charset=utf-8")
    cible.deposer(
        "etat.json",
        json.dumps(etat, ensure_ascii=False).encode("utf-8"),
        "application/json; charset=utf-8",
    )
    return etat
