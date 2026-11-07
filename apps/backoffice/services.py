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
from apps.core.models import Audit
from apps.credits import services as credits_services
from apps.credits.models import Grille, VersionGrille
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


# Clôture d'un compte (T-89)


@transaction.atomic
def cloturer_compte(client, *, agent, motif: str, adresse_ip=None) -> list:
    """Sur demande écrite du client : le compte est clos (plus d'envoi, plus de
    connexion), puis chaque campagne en cours ou programmée est annulée, ce qui
    libère ses réservations. Rend les campagnes annulées. Le remboursement du
    solde se saisit à part (paiements.rembourser_cloture), et les contacts sont
    effacés par l'ordonnanceur trente jours plus tard."""
    from apps.campagnes import services as campagnes
    from apps.campagnes.models import Campagne
    from apps.comptes.services import clore

    clore(client, auteur=agent, motif=motif, adresse_ip=adresse_ip)
    annulees = []
    en_cours = Campagne.objects.filter(client=client, etat__in=campagnes.ANNULABLES)
    for campagne in en_cours.order_by("cree_le"):
        annulees.append(campagnes.annuler(campagne, auteur=agent))
    return annulees


# Rôles des agents (T-95)


class AttributionRefusee(ValueError):
    pass


@transaction.atomic
def attribuer_roles(agent, roles: list[str], *, auteur, motif: str, adresse_ip=None) -> list:
    """Donne à un agent exactement ces rôles, parmi Support, Exploitation et
    Finance. Seul un super-administrateur attribue des rôles, jamais à lui-même,
    et toujours avec un motif ; la trace porte les rôles avant et après."""
    from django.contrib.auth.models import Group

    from .roles import ROLES

    if not getattr(auteur, "is_superuser", False):
        raise AttributionRefusee("Seul un super-administrateur attribue des rôles.")
    if agent.pk == auteur.pk:
        raise AttributionRefusee("Un agent ne change pas ses propres rôles.")
    if not agent.is_staff or agent.client_id:
        raise AttributionRefusee("Les rôles ne se donnent qu'aux agents EL-IMMEKA.")
    if not motif or not motif.strip():
        raise AttributionRefusee("Indiquez le motif du changement.")
    inconnus = set(roles) - set(ROLES)
    if inconnus:
        raise AttributionRefusee(f"Rôle inconnu : {', '.join(sorted(inconnus))}.")
    avant = sorted(agent.groups.filter(name__in=ROLES).values_list("name", flat=True))
    agent.groups.set(Group.objects.filter(name__in=roles))
    apres = sorted(roles)
    auditer(
        "backoffice.roles",
        agent,
        auteur=auteur,
        avant={"roles": avant},
        apres={"roles": apres},
        motif=motif.strip(),
        adresse_ip=adresse_ip,
    )
    return apres


# Suspension, réactivation et crédits d'un compte (T-95)


@dataclass(frozen=True)
class EnCours:
    campagne: object
    # Messages qui ne sont pas encore partis et que la pause retient.
    non_partis: int


def apercu_suspension(client) -> list:
    """Ce que la suspension arrêtera, montré à l'agent avant qu'il confirme :
    chaque campagne en attente ou en cours, et ses messages pas encore partis.
    Ne change rien."""
    from apps.campagnes import selectors as suivis
    from apps.campagnes import services as campagnes
    from apps.campagnes.models import Campagne

    en_cours = Campagne.objects.filter(client=client, etat__in=campagnes.PAUSABLES)
    apercu = []
    for campagne in en_cours.order_by("cree_le"):
        suivi = suivis.suivi(campagne)
        non_partis = suivi.non_soumis if suivi else campagne.destinataires_prevus or 0
        apercu.append(EnCours(campagne, non_partis))
    return apercu


@transaction.atomic
def suspendre_compte(client, *, agent, motif: str, adresse_ip=None) -> list:
    """Met en pause chaque campagne en attente ou en cours, puis suspend le
    compte : ses messages en file sont retenus, ceux qui viennent d'ailleurs
    sont refusés avant le réseau, et rien ne se confirme ni ne reprend. La trace
    de la suspension nomme les campagnes mises en pause : la réactivation ne
    reprendra qu'elles. `agent` est nul pour une suspension automatique."""
    from apps.campagnes import services as campagnes
    from apps.campagnes.models import Campagne
    from apps.comptes.services import suspendre

    en_cours = Campagne.objects.filter(client=client, etat__in=campagnes.PAUSABLES)
    mises_en_pause = [
        campagnes.mettre_en_pause(campagne, auteur=agent)
        for campagne in en_cours.order_by("cree_le")
    ]
    suspendre(
        client,
        auteur=agent,
        motif=motif,
        adresse_ip=adresse_ip,
        details={"campagnes_en_pause": [str(c.id) for c in mises_en_pause]},
    )
    return mises_en_pause


@transaction.atomic
def reactiver_compte(client, *, agent, motif: str, adresse_ip=None) -> list:
    """Réactive le compte, puis reprend les campagnes que sa dernière suspension
    avait mises en pause et qui le sont encore ; celles que le client avait
    lui-même mises en pause le restent."""
    from apps.campagnes import services as campagnes
    from apps.campagnes.models import Campagne
    from apps.comptes.services import reactiver

    suspension = (
        Audit.objects.filter(
            action="comptes.suspension", objet_type="comptes.client", objet_id=str(client.id)
        )
        .order_by("-horodatage")
        .first()
    )
    ids = (suspension.valeur_apres or {}).get("campagnes_en_pause", []) if suspension else []
    reactiver(client, auteur=agent, motif=motif, adresse_ip=adresse_ip)
    reprises = [
        campagnes.reprendre(campagne, auteur=agent)
        for campagne in Campagne.objects.filter(
            client=client, id__in=ids, etat=Campagne.Etat.EN_PAUSE
        ).order_by("cree_le")
    ]
    return reprises


class CreditRefuse(ValueError):
    pass


@transaction.atomic
def attribuer_credits(client, montant: int, *, agent, motif: str, adresse_ip=None):
    """Crédits offerts par un agent, geste commercial ou réparation : un
    recrédit au registre, avec son motif, et sa trace au journal d'audit."""
    from apps.credits import services as credits

    motif = (motif or "").strip()
    if not motif:
        raise CreditRefuse("Une attribution de crédits exige un motif.")
    if not isinstance(montant, int) or isinstance(montant, bool) or montant <= 0:
        raise CreditRefuse("Le montant est un nombre entier de FCFA, positif.")
    ecriture = credits.recrediter(client.id, montant, motif=motif, auteur=agent)
    auditer(
        "credits.attribution",
        client,
        auteur=agent,
        client=client,
        apres={"montant": montant, "ecriture": str(ecriture.id)},
        motif=motif,
        adresse_ip=adresse_ip,
    )
    return ecriture


# Grilles tarifaires (T-98). Seul un super-administrateur crée, prépare et
# publie (décision du 6 novembre 2026, sans double validation).


@transaction.atomic
def preparer_version(
    grille: Grille, *, agent, debut, fin=None, copier: bool = True, maintenant=None
) -> VersionGrille:
    """Ouvre une version en brouillon, datée : un changement de prix sans date
    de prise d'effet est refusé. Reprend, si demandé, les prix de la version
    en vigueur, pour n'avoir à saisir que ce qui change."""
    credits_services._super_administrateur(agent)
    maintenant = maintenant or timezone.now()
    if debut is None:
        raise credits_services.TarifInvalide(
            "Un changement de prix porte sa date de prise d'effet."
        )
    if debut < maintenant:
        raise credits_services.TarifInvalide("Une version ne prend pas effet dans le passé.")
    version = credits_services.creer_version(grille, debut=debut, fin=fin)
    en_vigueur = credits_services.version_en_vigueur_de(grille, maintenant)
    if copier and en_vigueur:
        for palier in en_vigueur.paliers.all():
            credits_services.fixer_palier(
                version,
                pays=palier.pays,
                operateur=palier.operateur,
                volume_min=palier.volume_min,
                prix_unitaire=palier.prix_unitaire,
            )
    return version


def publier_grille_version(version: VersionGrille, *, agent, maintenant=None) -> VersionGrille:
    """Publication depuis l'écran : la version porte sa date, puis la règle de
    `credits.services.publier_version` (complète, jamais dans le passé, la
    précédente reçoit sa fin, trace avec anciens et nouveaux prix)."""
    if version.debut is None:
        raise credits_services.TarifInvalide(
            "Un changement de prix porte sa date de prise d'effet."
        )
    return credits_services.publier_version(version, auteur=agent, maintenant=maintenant)


# Suspension automatique (T-101)


def suspendre_sur_alertes(maintenant=None) -> list:
    """Suspend chaque compte actif dont l'alerte du plafond est devenue
    critique, comme le ferait un agent (campagnes en pause, reprises à la
    réactivation), sans auteur. Une alerte ne suspend qu'une fois : un agent
    qui réactive le compte avant de la traiter n'est pas contredit. Appelée par
    l'ordonnanceur, sous le rôle worker."""
    from apps.campagnes.services import CampagneRefusee
    from apps.comptes.services import SuspensionRefusee
    from apps.conformite.models import AlerteFraude, RegleFraude

    maintenant = maintenant or timezone.now()
    suspendus = []
    alertes = AlerteFraude.objects.filter(
        regle=RegleFraude.Code.PLAFOND_COMPTE,
        gravite=AlerteFraude.Gravite.CRITIQUE,
        traitee_le__isnull=True,
    ).select_related("client")
    for alerte in alertes:
        details = alerte.details
        # L'alerte reste critique jusqu'à son traitement, mais ses détails
        # suivent la dernière période : seul un franchissement du seuil
        # critique suspend, une fois.
        critique = details.get("seuil_critique")
        if "suspension" in details or not critique or details.get("essais", 0) < critique:
            continue
        with transaction.atomic():
            if alerte.client.etat == Client.Etat.ACTIF:
                try:
                    suspendre_compte(
                        alerte.client,
                        agent=None,
                        motif=(
                            f"Suspension automatique : {details.get('essais')} envois tentés "
                            f"pour un plafond {details.get('periode')} de "
                            f"{details.get('plafond')}."
                        ),
                    )
                    suspendus.append(alerte.client)
                except SuspensionRefusee, CampagneRefusee:
                    pass
            alerte.details = {**details, "suspension": maintenant.isoformat()}
            alerte.save(update_fields=["details", "modifie_le"])
    return suspendus
