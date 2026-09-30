"""Porte d'écriture des expéditeurs : déclaration (T-58), décisions des
opérateurs saisies par un agent (T-59).

Un identifiant s'écrit en onze caractères au plus, lettres sans accent et
chiffres, avec au moins une lettre : un identifiant fait de chiffres se
confondrait avec un numéro. Un nom réservé (opérateur, institution, marque,
mot générique) n'est jamais refusé ni activé d'office : il part en revue
manuelle, et aucun dossier n'est déposé chez un opérateur tant qu'un agent n'a
pas levé cette revue, motif à l'appui.
"""

import re
import unicodedata

from django.db import transaction
from django.utils import timezone

from apps.core.audit import auditer
from apps.core.evenements import publier
from apps.fichiers import services as fichiers
from apps.fichiers.models import Fichier

from .models import OPERATEURS, Expediteur, ExpediteurReseau, Reserve

LONGUEUR_MAX = 11
ALPHABET = re.compile(r"^[A-Za-z0-9]+$")


class ExpediteurRefuse(ValueError):
    pass


class DecisionRefusee(ValueError):
    pass


def _cle(texte: str) -> str:
    """Forme de comparaison : majuscules, sans accent ni signe."""
    sans_accent = unicodedata.normalize("NFKD", texte).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", sans_accent.upper())


def verifier_libelle(libelle: str) -> str:
    libelle = libelle.strip()
    if not libelle:
        raise ExpediteurRefuse("Saisissez l'identifiant d'expéditeur.")
    if len(libelle) > LONGUEUR_MAX:
        raise ExpediteurRefuse(f"{LONGUEUR_MAX} caractères au plus ({len(libelle)} saisis).")
    if not (libelle.isascii() and ALPHABET.match(libelle)):
        raise ExpediteurRefuse(
            "Lettres sans accent et chiffres seulement, sans espace ni signe : "
            "c'est ce que les trois réseaux affichent tous."
        )
    if not any(c.isalpha() for c in libelle):
        raise ExpediteurRefuse(
            "Au moins une lettre : un identifiant fait de chiffres se confond avec un numéro."
        )
    return libelle


class FichierReservesInvalide(ValueError):
    pass


def charger_reserves(texte: str) -> int:
    """Ajoute ou met à jour les noms réservés d'un fichier « libellé;motif » ;
    n'en retire aucun. Réservé au back-office."""
    lignes = [ligne.split(";") for ligne in texte.splitlines() if ligne.strip()]
    if lignes and lignes[0][0].strip().lower() == "libelle":
        lignes = lignes[1:]
    for rang, ligne in enumerate(lignes, start=2):
        if len(ligne) != 2 or not _cle(ligne[0]) or not ligne[1].strip():
            raise FichierReservesInvalide(f"Ligne {rang} : « libellé;motif » attendu.")
    with transaction.atomic():
        for libelle, motif in lignes:
            Reserve.objects.update_or_create(
                libelle=_cle(libelle), defaults={"motif": motif.strip(), "actif": True}
            )
    return len(lignes)


def reserve_touchee(libelle: str) -> Reserve | None:
    """Le nom réservé que l'identifiant contient, s'il en contient un :
    « ORANGEPROMO » touche « ORANGE »."""
    cle = _cle(libelle)
    for reserve in Reserve.objects.filter(actif=True).order_by("libelle"):
        if _cle(reserve.libelle) and _cle(reserve.libelle) in cle:
            return reserve
    return None


def declarer(*, client, libelle: str, mandat, nom_mandat: str, auteur) -> Expediteur:
    """Déclare un identifiant, avec sa pièce de mandat (flux binaire). Crée une
    ligne par réseau, NON_SOUMIS : c'est un agent qui dépose le dossier chez
    chaque opérateur."""
    libelle = verifier_libelle(libelle)
    if Expediteur.objects.filter(client=client, libelle__iexact=libelle).exists():
        raise ExpediteurRefuse(f"L'identifiant {libelle} est déjà déclaré.")
    try:
        piece = fichiers.deposer(
            client=client, famille=Fichier.Famille.PIECE, flux=mandat, nom_origine=nom_mandat
        )
    except fichiers.FichierRefuse as refus:
        raise ExpediteurRefuse(f"Pièce de mandat : {refus}") from None
    reserve = reserve_touchee(libelle)
    with transaction.atomic():
        expediteur = Expediteur.objects.create(
            client=client,
            libelle=libelle,
            etat=Expediteur.Etat.SOUMIS,
            justificatif_id=piece.id,
            revue_manuelle=reserve is not None,
            motif_revue=f"Nom réservé : {reserve.libelle} ({reserve.motif})" if reserve else "",
        )
        ExpediteurReseau.objects.bulk_create(
            ExpediteurReseau(client=client, expediteur=expediteur, operateur=operateur)
            for operateur in OPERATEURS
        )
        auditer(
            "expediteurs.declaration",
            expediteur,
            auteur=auteur,
            client=client,
            apres={"libelle": libelle, "revue_manuelle": expediteur.revue_manuelle},
        )
    return expediteur


# Décisions, par un agent du back-office


def _reseau_verrouille(expediteur_id, operateur: str) -> ExpediteurReseau:
    try:
        return (
            ExpediteurReseau.objects.select_for_update()
            .select_related("expediteur")
            .get(expediteur_id=expediteur_id, operateur=operateur)
        )
    except ExpediteurReseau.DoesNotExist:
        raise DecisionRefusee(f"Aucune ligne {operateur} pour cet identifiant.") from None


@transaction.atomic
def lever_revue(expediteur_id, *, agent, motif: str, adresse_ip=None) -> Expediteur:
    """Autorise le dépôt d'un nom réservé, après vérification du mandat."""
    motif = motif.strip()
    if not motif:
        raise DecisionRefusee("Le motif de la levée de revue est obligatoire.")
    expediteur = Expediteur.objects.select_for_update().get(id=expediteur_id)
    if not expediteur.revue_manuelle:
        raise DecisionRefusee("Cet identifiant n'est pas en revue manuelle.")
    avant = {"revue_manuelle": True, "motif_revue": expediteur.motif_revue}
    expediteur.revue_manuelle = False
    expediteur.save(update_fields=["revue_manuelle", "modifie_le"])
    auditer(
        "expediteurs.revue.levee",
        expediteur,
        auteur=agent,
        client=expediteur.client,
        avant=avant,
        apres={"revue_manuelle": False},
        motif=motif,
        adresse_ip=adresse_ip,
    )
    return expediteur


@transaction.atomic
def deposer_dossier(
    expediteur_id, operateur: str, *, agent, reference: str, depose_le=None, adresse_ip=None
) -> ExpediteurReseau:
    """Le dossier est déposé chez l'opérateur : la ligne passe en instruction."""
    reseau = _reseau_verrouille(expediteur_id, operateur)
    if reseau.expediteur.revue_manuelle:
        raise DecisionRefusee(
            f"{reseau.expediteur} porte un nom réservé : levez d'abord la revue manuelle."
        )
    if reseau.etat not in (ExpediteurReseau.Etat.NON_SOUMIS, ExpediteurReseau.Etat.REFUSE):
        raise DecisionRefusee(f"Dossier déjà {reseau.get_etat_display().lower()} chez {operateur}.")
    if not reference.strip():
        raise DecisionRefusee("La référence du dossier chez l'opérateur est obligatoire.")
    avant = {"etat": reseau.etat}
    reseau.etat = ExpediteurReseau.Etat.EN_INSTRUCTION
    reseau.reference_dossier = reference.strip()
    reseau.depose_le = depose_le or timezone.now()
    reseau.decide_le, reseau.motif_refus = None, ""
    reseau.save()
    auditer(
        "expediteurs.reseau.depot",
        reseau,
        auteur=agent,
        client=reseau.expediteur.client,
        avant=avant,
        apres={"etat": reseau.etat, "operateur": operateur, "reference": reseau.reference_dossier},
        adresse_ip=adresse_ip,
    )
    return reseau


@transaction.atomic
def decider(
    expediteur_id,
    operateur: str,
    *,
    agent,
    approuve: bool,
    decide_le,
    reference: str,
    motif: str = "",
    expire_le=None,
    adresse_ip=None,
) -> ExpediteurReseau:
    """La décision de l'opérateur, avec sa date, la référence de son dossier et,
    pour un refus, son motif. L'identifiant devient actif dès qu'un réseau l'a
    approuvé ; il reste soumis tant qu'aucun ne l'a fait."""
    reseau = _reseau_verrouille(expediteur_id, operateur)
    if reseau.etat != ExpediteurReseau.Etat.EN_INSTRUCTION:
        raise DecisionRefusee(
            f"Aucun dossier en instruction chez {operateur} : déposez-le d'abord."
        )
    if decide_le is None or not reference.strip():
        raise DecisionRefusee(
            "La date de la décision et la référence du dossier sont obligatoires."
        )
    if not approuve and not motif.strip():
        raise DecisionRefusee("Le motif du refus est obligatoire.")
    avant = {"etat": reseau.etat}
    reseau.etat = ExpediteurReseau.Etat.APPROUVE if approuve else ExpediteurReseau.Etat.REFUSE
    reseau.decide_le = decide_le
    reseau.reference_dossier = reference.strip()
    reseau.motif_refus = "" if approuve else motif.strip()
    reseau.expire_le = expire_le if approuve else None
    reseau.save()
    expediteur = reseau.expediteur
    approuve_quelque_part = ExpediteurReseau.objects.filter(
        expediteur=expediteur, etat=ExpediteurReseau.Etat.APPROUVE
    ).exists()
    etat = Expediteur.Etat.ACTIF if approuve_quelque_part else Expediteur.Etat.SOUMIS
    if expediteur.etat != etat and expediteur.etat != Expediteur.Etat.SUSPENDU:
        expediteur.etat = etat
        expediteur.save(update_fields=["etat", "modifie_le"])
    if approuve:
        # Les campagnes en attente de cette approbation reprennent sur ce réseau.
        publier(
            "expediteurs.reseau.approuve",
            client_id=expediteur.client_id,
            objet_id=reseau.id,
            auteur_id=agent.id if agent else None,
            donnees={"libelle": expediteur.libelle, "operateur": operateur},
        )
    auditer(
        "expediteurs.reseau.decision",
        reseau,
        auteur=agent,
        client=expediteur.client,
        avant=avant,
        apres={
            "etat": reseau.etat,
            "operateur": operateur,
            "reference": reseau.reference_dossier,
            "decide_le": decide_le.isoformat(),
        },
        motif=reseau.motif_refus,
        adresse_ip=adresse_ip,
    )
    return reseau
