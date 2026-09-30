"""Porte d'écriture du back-office : les actions des agents EL-IMMEKA.

Ces actions touchent plusieurs modules (comptes, fichiers, conformité) : elles
vivent ici, dans la couche la plus haute, qui peut tous les appeler.
"""

from dataclasses import dataclass

from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.comptes.models import Client, Dossier, Piece, Utilisateur, pieces_attendues
from apps.comptes.services import creer_client, creer_utilisateur, envoyer_invitation
from apps.core.audit import auditer
from apps.fichiers.models import Fichier
from apps.fichiers.services import FichierRefuse, deposer


class EnregistrementRefuse(ValueError):
    """Porte, par champ, ce qui manque ou ne va pas, pour que l'agent sache
    exactement quoi corriger."""

    def __init__(self, erreurs: dict[str, list[str]]):
        self.erreurs = erreurs
        super().__init__("; ".join(m for messages in erreurs.values() for m in messages))


@dataclass(frozen=True)
class PieceDeposee:
    contenu: bytes
    nom: str


def _verifier(identite: dict, pieces: dict[str, PieceDeposee]) -> dict[str, list[str]]:
    erreurs: dict[str, list[str]] = {}
    forme = identite.get("forme")
    if forme not in Client.Forme.values:
        return {"forme": ["Forme juridique à choisir."]}
    for champ, libelle in (("raison_sociale", "La raison sociale"), ("ifu", "Le numéro IFU")):
        if not (identite.get(champ) or "").strip():
            erreurs.setdefault(champ, []).append(f"{libelle} est obligatoire.")
    if forme == Client.Forme.SOCIETE and not (identite.get("rccm") or "").strip():
        erreurs.setdefault("rccm", []).append(
            "Le numéro RCCM est obligatoire pour une société commerciale."
        )
    for champ, libelle in (
        ("representant_nom", "Le nom"),
        ("representant_courriel", "Le courriel"),
    ):
        if not (identite.get(champ) or "").strip():
            erreurs.setdefault(champ, []).append(f"{libelle} du représentant est obligatoire.")
    for type_piece in pieces_attendues(forme):
        if type_piece not in pieces:
            libelle = Piece.Type(type_piece).label
            erreurs.setdefault(f"piece_{type_piece}", []).append(f"Pièce manquante : {libelle}.")
    rccm = (identite.get("rccm") or "").strip()
    if rccm and Client.objects.filter(rccm=rccm).exists():
        erreurs.setdefault("rccm", []).append("Ce numéro RCCM est déjà rattaché à un compte.")
    ifu = (identite.get("ifu") or "").strip()
    if ifu and Client.objects.filter(ifu=ifu).exists():
        erreurs.setdefault("ifu", []).append("Ce numéro IFU est déjà rattaché à un compte.")
    return erreurs


@transaction.atomic
def enregistrer_client(
    *, identite: dict, pieces: dict[str, PieceDeposee], agent, adresse_ip=None
) -> Dossier:
    """Enregistre un client signé, son dossier et ses pièces, et crée son premier
    administrateur, sans mot de passe tant qu'il n'a pas accepté son invitation.

    `identite` : raison_sociale, forme, rccm, ifu, contact_facturation,
    representant_nom, representant_courriel, representant_telephone.
    Lève EnregistrementRefuse en nommant chaque manque ; rien n'est écrit alors.
    """
    erreurs = _verifier(identite, pieces)
    if erreurs:
        raise EnregistrementRefuse(erreurs)
    try:
        client = creer_client(
            raison_sociale=identite["raison_sociale"].strip(),
            forme=identite["forme"],
            rccm=(identite.get("rccm") or "").strip() or None,
            ifu=identite["ifu"].strip(),
            contact_facturation=(identite.get("contact_facturation") or "").strip(),
        )
    except IntegrityError:
        # Deux enregistrements simultanés du même numéro : la contrainte de base tranche.
        raise EnregistrementRefuse(
            {"rccm": ["Ce numéro RCCM ou IFU est déjà rattaché à un compte."]}
        ) from None
    dossier = Dossier.objects.create(
        client=client, etat=Dossier.Etat.DEPOSE, depose_le=timezone.now()
    )
    for type_piece, piece in pieces.items():
        try:
            fichier = deposer(
                client=client,
                famille=Fichier.Famille.PIECE,
                contenu=piece.contenu,
                nom_origine=piece.nom,
            )
        except FichierRefuse as refus:
            raise EnregistrementRefuse({f"piece_{type_piece}": [str(refus)]}) from None
        Piece.objects.create(client=client, dossier=dossier, type=type_piece, fichier_id=fichier.id)
    representant = creer_utilisateur(
        courriel=identite["representant_courriel"],
        mot_de_passe=None,
        nom=identite["representant_nom"].strip(),
        client=client,
        role=Utilisateur.Role.ADMIN,
        telephone=(identite.get("representant_telephone") or "").strip(),
    )
    envoyer_invitation(representant)
    auditer(
        "comptes.client.enregistre",
        client,
        auteur=agent,
        client=client,
        apres={
            "raison_sociale": client.raison_sociale,
            "forme": client.forme,
            "rccm": client.rccm,
            "ifu": client.ifu,
            "pieces": sorted(pieces),
        },
        adresse_ip=adresse_ip,
    )
    return dossier


# Les cinq contrôles de la revue humaine (fiche F-11).
CONTROLES_DE_REVUE = (
    "Raison sociale concordante entre la pièce d'existence, l'IFU et le compte",
    "Numéro déclaré retrouvé dans le fichier national du RCCM",
    "Identité du représentant cohérente avec la pièce d'existence, ou procuration",
    "Pièces lisibles et non expirées",
    "Activité déclarée cohérente avec l'usage annoncé des SMS",
)


class DecisionRefusee(ValueError):
    pass


def _dossier_a_decider(dossier_id) -> Dossier:
    dossier = Dossier.objects.select_for_update().select_related("client").get(id=dossier_id)
    if dossier.etat not in (Dossier.Etat.DEPOSE, Dossier.Etat.EN_REVUE):
        raise DecisionRefusee(f"Dossier déjà {dossier.get_etat_display().lower()}.")
    return dossier


@transaction.atomic
def approuver_dossier(
    dossier_id, *, agent, palier=Client.Palier.VERIFIE, controles=(), adresse_ip=None
) -> Dossier:
    """Approuve un dossier : le client passe au palier accordé, ses plafonds
    suivent sans autre intervention, ses pièces sont acceptées."""
    from apps.comptes.services import PLAFONDS_PAR_PALIER
    from apps.core.evenements import publier

    manquants = [c for c in CONTROLES_DE_REVUE if c not in controles]
    if manquants:
        raise DecisionRefusee("Contrôles non cochés : " + " ; ".join(manquants))
    dossier = _dossier_a_decider(dossier_id)
    client = dossier.client
    avant = {"palier": client.palier, "etat_dossier": dossier.etat}
    plafonds = PLAFONDS_PAR_PALIER[palier]
    Client.objects.filter(id=client.id).update(
        palier=palier,
        plafond_horaire=plafonds["horaire"],
        plafond_journalier=plafonds["journalier"],
        modifie_le=timezone.now(),
    )
    dossier.etat = Dossier.Etat.APPROUVE
    dossier.decide_le, dossier.decide_par = timezone.now(), agent
    dossier.palier_accorde = palier
    dossier.save(update_fields=["etat", "decide_le", "decide_par", "palier_accorde", "modifie_le"])
    Piece.objects.filter(dossier=dossier).update(etat=Piece.Etat.ACCEPTEE)
    auditer(
        "comptes.dossier.approuve",
        dossier,
        auteur=agent,
        client=client,
        avant=avant,
        apres={"palier": palier, "etat_dossier": dossier.etat, "controles": list(controles)},
        adresse_ip=adresse_ip,
    )
    publier(
        "compte.dossier.approuve",
        client_id=client.id,
        objet_id=dossier.id,
        auteur_id=agent.id,
        donnees={"palier": palier},
    )
    return dossier


@transaction.atomic
def rejeter_dossier(dossier_id, *, agent, motif: str, detail: str = "", adresse_ip=None) -> Dossier:
    """Rejette un dossier. Le motif vient de la liste fermée, jamais d'un champ
    libre ; son libellé part au client mot pour mot, avec le détail éventuel."""
    from apps.core.evenements import publier

    if motif not in Dossier.MotifRejet.values:
        raise DecisionRefusee("Un rejet exige un motif pris dans la liste des motifs.")
    dossier = _dossier_a_decider(dossier_id)
    avant = {"etat_dossier": dossier.etat}
    dossier.etat = Dossier.Etat.REJETE
    dossier.decide_le, dossier.decide_par = timezone.now(), agent
    dossier.motif_rejet, dossier.motif_detail = motif, detail.strip()
    dossier.save(
        update_fields=[
            "etat",
            "decide_le",
            "decide_par",
            "motif_rejet",
            "motif_detail",
            "modifie_le",
        ]
    )
    libelle = Dossier.MotifRejet(motif).label
    auditer(
        "comptes.dossier.rejete",
        dossier,
        auteur=agent,
        client=dossier.client,
        avant=avant,
        apres={"etat_dossier": dossier.etat, "motif": motif, "detail": dossier.motif_detail},
        motif=libelle,
        adresse_ip=adresse_ip,
    )
    publier(
        "compte.dossier.rejete",
        client_id=dossier.client_id,
        objet_id=dossier.id,
        auteur_id=agent.id,
        donnees={"motif": motif, "libelle": libelle, "detail": dossier.motif_detail},
    )
    return dossier
