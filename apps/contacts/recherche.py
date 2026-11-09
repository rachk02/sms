"""Recherche et export de contacts (T-53).

La recherche par numéro passe par la colonne normalisée, par préfixe : le client
tape le début d'un numéro sous la forme qu'il veut (« 70 12 », « +226 7012 »,
« 00226... »), la recherche le ramène au format international. Aucune requête
n'emploie de motif ouvert à gauche : l'index sert, et un répertoire de deux
millions de contacts répond aussi vite qu'un de mille. Les index portent des
classes d'opérateurs de motif, la collation de la base n'étant pas « C ».

La pagination est par curseur sur (cree_le, id), du plus récent au plus ancien :
une page lointaine coûte autant que la première, et un contact ajouté entre deux
pages ne décale rien.

Au-delà de l'écran, c'est un export : la demande part en file, le worker produit
le fichier dans le stockage objet et le demandeur reçoit par courriel un lien
du portail (décision A4 du 9 novembre 2026). Ce lien exige la connexion et ne
sert qu'au demandeur, sept jours durant ; il renvoie alors vers un lien signé
de cinq minutes. Un courriel transféré ou intercepté ne donne donc pas accès au
répertoire.
"""

import base64
import binascii
import csv
import io
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta

from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from apps.comptes.selectors import membre_actif
from apps.core import courriels
from apps.core.evenements import publier
from apps.core.tableur import cellules
from apps.fichiers import services as fichiers
from apps.fichiers.models import Fichier

from . import segments
from .models import ChampPersonnalise, Contact, Export

TAILLE_PAGE = 50
# Au-delà, l'écran n'affiche plus : il propose l'export.
SEUIL_AFFICHAGE = 10_000
# Le portail sert l'export sept jours ; le lien signé qu'il rend vit cinq minutes.
DUREE_EXPORT = timedelta(days=7)
DUREE_LIEN_EXPORT = timedelta(minutes=5)
FILE_EXPORT = "import.contacts"
PAQUET = 2_000


class RechercheInvalide(ValueError):
    pass


class ExportIntrouvable(LookupError):
    """Inconnu, d'un autre compte, d'un autre demandeur ou pas encore produit :
    la même réponse dans tous les cas."""


class ExportExpire(Exception):
    pass


@dataclass(frozen=True)
class Filtres:
    numero: str = ""
    nom: str = ""
    liste_id: str = ""
    operateur: str = ""
    depuis: str = ""
    jusqu_au: str = ""

    def criteres(self) -> list[dict]:
        """Les filtres communs avec les segments, sous leur forme de critères."""
        criteres = []
        if self.operateur:
            criteres.append({"champ": "operateur", "operation": "egal", "valeur": self.operateur})
        if self.liste_id:
            criteres.append({"champ": "liste", "operation": "egal", "valeur": self.liste_id})
        if self.depuis:
            criteres.append({"champ": "date_entree", "operation": "apres", "valeur": self.depuis})
        if self.jusqu_au:
            criteres.append({"champ": "date_entree", "operation": "avant", "valeur": self.jusqu_au})
        return criteres


def prefixes_numero(saisie: str) -> list[str]:
    """Les débuts de numéro international que la saisie peut désigner."""
    chiffres = re.sub(r"\D", "", saisie)
    if not chiffres:
        return []
    if saisie.strip().startswith("+") or chiffres.startswith("00"):
        return ["+" + chiffres.removeprefix("00")]
    if chiffres.startswith("226") and len(chiffres) > 3:
        # « 22670... » : indicatif tapé sans le « + », ou numéro local en 22.
        return ["+" + chiffres, "+226" + chiffres]
    return ["+226" + chiffres]


def filtrer(client, filtres: Filtres):
    """Les contacts du client qui répondent aux filtres, actifs ou non, liste
    noire comprise : la recherche montre le répertoire tel qu'il est."""
    try:
        requete = segments.filtrer(client, filtres.criteres())
    except segments.SegmentInvalide as erreur:
        raise RechercheInvalide(str(erreur)) from None
    if filtres.numero:
        prefixes = prefixes_numero(filtres.numero)
        if not prefixes:
            raise RechercheInvalide("Un numéro se cherche par ses chiffres.")
        condition = Q()
        for prefixe in prefixes:
            condition |= Q(numero_normalise__startswith=prefixe)
        requete = requete.filter(condition)
    if filtres.nom.strip():
        requete = requete.filter(nom__istartswith=filtres.nom.strip())
    return requete


def _coder(contact) -> str:
    brut = f"{contact.cree_le.isoformat()}|{contact.id}".encode()
    return base64.urlsafe_b64encode(brut).decode().rstrip("=")


def _decoder(curseur: str) -> tuple[datetime, uuid.UUID]:
    try:
        brut = base64.urlsafe_b64decode(curseur + "=" * (-len(curseur) % 4)).decode()
        instant, identifiant = brut.split("|")
        return datetime.fromisoformat(instant), uuid.UUID(identifiant)
    except binascii.Error, UnicodeDecodeError, ValueError:
        raise RechercheInvalide("Curseur de pagination invalide.") from None


def page(client, filtres: Filtres, apres: str = "") -> tuple[list[Contact], str]:
    """Une page de résultats et le curseur de la suivante (vide à la dernière)."""
    requete = filtrer(client, filtres)
    if apres:
        instant, identifiant = _decoder(apres)
        requete = requete.filter(Q(cree_le__lt=instant) | Q(cree_le=instant, id__lt=identifiant))
    contacts = list(requete.order_by("-cree_le", "-id")[: TAILLE_PAGE + 1])
    suivante = _coder(contacts[TAILLE_PAGE - 1]) if len(contacts) > TAILLE_PAGE else ""
    return contacts[:TAILLE_PAGE], suivante


def depasse_l_affichage(client, filtres: Filtres) -> bool:
    """Vrai au-delà du seuil : compte au plus seuil plus un, jamais tout."""
    return filtrer(client, filtres)[SEUIL_AFFICHAGE : SEUIL_AFFICHAGE + 1].exists()


# Export


def demander_export(*, client, demandeur, filtres: Filtres) -> Export:
    """Enregistre la demande et la met en file ; le lien part par courriel au
    demandeur. Tracé au journal d'audit : c'est une sortie de données
    personnelles."""
    from apps.core.audit import auditer

    filtrer(client, filtres)  # des filtres invalides sont refusés ici, pas dans le worker
    with transaction.atomic():
        export = Export.objects.create(
            client=client, courriel=demandeur.courriel, filtres=filtres.__dict__
        )
        auditer(
            "contacts.export.demande",
            export,
            auteur=demandeur,
            client=client,
            apres={"filtres": filtres.__dict__},
        )
        publier(
            FILE_EXPORT,
            echange="sms",
            client_id=client.id,
            objet_id=export.id,
            donnees={"export_id": str(export.id)},
            cle_idempotence=f"export:{export.id}",
        )
    return export


def _lignes(client, filtres: Filtres):
    noms = list(
        ChampPersonnalise.objects.filter(client=client)
        .order_by("libelle")
        .values_list("nom", "libelle")
    )
    yield [
        "numero",
        "nom",
        "prenom",
        "ville",
        "operateur",
        "entre_le",
        "actif",
        *(libelle for _, libelle in noms),
    ]
    contacts = filtrer(client, filtres).order_by("cree_le", "id").iterator(chunk_size=PAQUET)
    for contact in contacts:
        yield [
            contact.numero_normalise,
            contact.nom,
            contact.prenom,
            contact.ville,
            contact.operateur_confirme or contact.operateur_presume,
            timezone.localtime(contact.cree_le).date().isoformat(),
            "oui" if contact.actif else "non",
            *(str(contact.champs.get(nom, "")) for nom, _ in noms),
        ]


def produire(export_id) -> Export:
    """Produit le fichier, par le worker. Relivré après un arrêt, l'export
    repart de zéro ; déjà terminé, il ne se refait pas."""
    export = Export.objects.select_related("client").get(id=export_id)
    if export.etat == Export.Etat.TERMINE:
        return export
    Export.objects.filter(id=export.id).update(etat=Export.Etat.EN_COURS)
    filtres = Filtres(**export.filtres)
    lignes = 0
    try:
        with tempfile.TemporaryFile() as temporaire:
            # Point-virgule et marque d'ordre : un tableur ouvre le fichier avec
            # ses accents, et il se réimporte tel quel.
            texte = io.TextIOWrapper(temporaire, encoding="utf-8-sig", newline="")
            ecrivain = csv.writer(texte, delimiter=";")
            for ligne in _lignes(export.client, filtres):
                ecrivain.writerow(cellules(ligne))
                lignes += 1
            texte.flush()
            texte.detach()
            temporaire.seek(0)
            fichier = fichiers.deposer(
                client=export.client,
                famille=Fichier.Famille.EXPORT,
                flux=temporaire,
                nom_origine=f"contacts-{timezone.localdate().isoformat()}.csv",
            )
    except Exception as erreur:
        Export.objects.filter(id=export.id).update(
            etat=Export.Etat.ECHOUE, erreur=repr(erreur)[:500]
        )
        raise
    with transaction.atomic():
        Export.objects.filter(id=export.id).update(
            etat=Export.Etat.TERMINE,
            fichier_id=fichier.id,
            lignes=lignes - 1,
            termine_le=timezone.now(),
        )
        # Le lien vers le répertoire ne part qu'au demandeur, et seulement s'il
        # est encore membre actif du compte : retiré entre-temps, il ne le
        # reçoit pas, et personne ne le reçoit à sa place.
        if membre_actif(export.client_id, export.courriel):
            courriels.envoyer(
                export.courriel,
                "Votre export de contacts est prêt",
                f"Votre export de {lignes - 1} contacts est prêt. Il se télécharge depuis "
                "votre espace, après connexion, pendant sept jours :\n\n"
                f"{settings.PORTAIL_URL}/contacts/exports/{export.id}\n",
            )
    export.refresh_from_db()
    return export


def lien_export(*, export_id, utilisateur) -> str:
    """Le lien signé de cinq minutes vers le fichier d'un export, pour son seul
    demandeur, membre du compte, dans les sept jours. Tracé au journal d'audit :
    c'est la sortie effective des données personnelles."""
    from apps.core.audit import auditer

    export = Export.objects.filter(
        id=export_id,
        client_id=utilisateur.client_id,
        courriel=utilisateur.courriel,
        etat=Export.Etat.TERMINE,
        fichier_id__isnull=False,
    ).first()
    if export is None:
        raise ExportIntrouvable
    fichier = Fichier.objects.filter(id=export.fichier_id, supprime_le__isnull=True).first()
    if fichier is None or export.termine_le < timezone.now() - DUREE_EXPORT:
        raise ExportExpire
    auditer("contacts.export.telechargement", export, auteur=utilisateur, client=utilisateur.client)
    return fichiers.lien(fichier, DUREE_LIEN_EXPORT)
