"""Lectures des campagnes."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from django.db.models import Sum
from django.utils import timezone

from apps.conformite import fenetre
from apps.contacts.models import ListeNoire
from apps.credits.models import Reservation
from apps.journal.models import Compteur

from .models import Campagne

COLONNES = ("emis", "livres", "echecs", "sans_accuse", "refuses", "annules")


@dataclass(frozen=True)
class Suivi:
    # Partis vers le réseau : ils ne se rattrapent pas.
    partis: int
    livres: int
    echecs: int
    sans_accuse: int
    # Refusés avant le réseau (liste noire, expéditeur, passerelle), sans frais.
    refuses: int
    annules: int
    # Pas encore partis : en file, retenus, en cours de soumission ou pas encore publiés.
    non_soumis: int
    # Coût consommé sur la réservation, plafonné au coût annoncé.
    cout_consomme: int
    # [(réseau, expéditeur, partis)], les plus nombreux d'abord.
    par_expediteur: list

    @property
    def progression(self) -> int:
        """Part des destinataires dont le sort est réglé, en pourcentage."""
        total = self.partis + self.refuses + self.annules + self.non_soumis
        return round(100 * (total - self.non_soumis) / total) if total else 0


def suivi(campagne: Campagne) -> Suivi | None:
    """Où en est une campagne confirmée, lu sur les compteurs pré-agrégés et sur
    sa réservation : une poignée de lignes, quel que soit le nombre de messages.
    Jamais de lecture de `journal_message`."""
    if not campagne.confirmee_le or campagne.recurrente:
        return None
    lignes = (
        Compteur.objects.filter(client_id=campagne.client_id, campagne_id=campagne.id)
        .values("operateur", "expediteur_libelle")
        .annotate(**{c: Sum(c) for c in COLONNES})
    )
    totaux = dict.fromkeys(COLONNES, 0)
    par_expediteur = []
    for ligne in lignes:
        for colonne in COLONNES:
            totaux[colonne] += ligne[colonne]
        if ligne["emis"]:
            par_expediteur.append((ligne["operateur"], ligne["expediteur_libelle"], ligne["emis"]))
    reste = campagne.destinataires_prevus - totaux["emis"] - totaux["refuses"]
    if campagne.etat == Campagne.Etat.ANNULEE:
        # Tout ce qui n'est pas parti ni refusé est annulé, ou en passe de l'être.
        annules, non_soumis = max(reste, 0), 0
    else:
        annules, non_soumis = totaux["annules"], max(reste - totaux["annules"], 0)
    if campagne.terminee_le:
        cout = campagne.cout_reel
    else:
        reservation = (
            Reservation.objects.filter(campagne_id=campagne.id)
            .values_list("montant_initial", "montant_restant")
            .first()
        )
        cout = reservation[0] - reservation[1] if reservation else 0
    return Suivi(
        partis=totaux["emis"],
        livres=totaux["livres"],
        echecs=totaux["echecs"],
        sans_accuse=totaux["sans_accuse"],
        refuses=totaux["refuses"],
        annules=annules,
        non_soumis=non_soumis,
        cout_consomme=cout,
        par_expediteur=sorted(par_expediteur, key=lambda ligne: -ligne[2]),
    )


def en_direct(campagne: Campagne) -> bool:
    """Vrai tant que la campagne peut encore bouger : le suivi se rafraîchit."""
    return campagne.etat in (
        Campagne.Etat.EN_ATTENTE,
        Campagne.Etat.EN_COURS,
        Campagne.Etat.EN_PAUSE,
    ) or (campagne.etat == Campagne.Etat.ANNULEE and not campagne.terminee_le)


@dataclass(frozen=True)
class Liberation:
    """Une campagne marketing retenue par la fenêtre légale (T-74) : l'heure où
    elle repartira seule, ou None si aucune ouverture n'est prévue."""

    prevue_le: datetime | None


def liberation(campagne: Campagne, maintenant: datetime | None = None) -> Liberation | None:
    """La libération d'une campagne qui attend l'ouverture de la fenêtre, None
    si elle n'attend pas : transactionnelle, en pause, close, ou dans la
    fenêtre. Calculée sur la fenêtre et les jours fériés en base, jamais
    approximée. Une campagne programmée hors de la fenêtre l'annonce dès sa
    programmation."""
    if campagne.type != Campagne.Type.MARKETING:
        return None
    if campagne.etat not in (
        Campagne.Etat.PLANIFIEE,
        Campagne.Etat.EN_ATTENTE,
        Campagne.Etat.EN_COURS,
    ):
        return None
    depart = maintenant or timezone.now()
    if campagne.etat == Campagne.Etat.PLANIFIEE and campagne.planifiee_pour:
        depart = max(depart, campagne.planifiee_pour)
    prevue = fenetre.prochaine_ouverture(depart, campagne.client.pays)
    if prevue == depart:
        return None
    return Liberation(prevue)


GRILLES = {"NEGOCIEE": "grille négociée, ", "PROMOTION": "promotion, "}


def explication_tarif(campagne: Campagne) -> list[str]:
    """Le tarif de la campagne, phrase par phrase, pour son rapport : figé à la
    confirmation, avec le volume du mois qui a fixé le palier. Vide pour une
    campagne confirmée avant le gel des tarifs."""
    tarification = campagne.tarification
    if not tarification:
        return []
    le = timezone.localtime(datetime.fromisoformat(tarification["le"]))
    lignes = [
        f"Tarif figé à la confirmation, le {le:%d/%m/%Y à %H:%M}, sur un volume de "
        f"{tarification['volume_mensuel']} SMS soumis depuis le début du mois."
    ]
    for reseau, detail in tarification["reseaux"].items():
        palier = (
            f"palier à partir de {detail['palier']} SMS dans le mois"
            if detail["palier"]
            else "premier palier"
        )
        grille = GRILLES.get(detail.get("grille"), "")
        lignes.append(f"{reseau} : {detail['prix']} FCFA par segment, {grille}{palier}.")
    if campagne.segments_prevus > 1:
        lignes.append(
            f"Chaque message compte {campagne.segments_prevus} segments au plus, "
            "facturés chacun à ce prix."
        )
    lignes.append(
        "Les SMS de cette campagne comptent dans le volume du mois des envois suivants ; "
        "un palier franchi ou une grille changée pendant sa diffusion ne change pas son prix."
    )
    return lignes


# Tableau de bord du client (T-92)

# Périodes proposées, des plus courtes aux plus longues ; la première est celle
# par défaut. Elles se comptent en heures entières, comme les compteurs.
PERIODES = {
    "7j": ("7 derniers jours", timedelta(days=7)),
    "24h": ("24 dernières heures", timedelta(hours=24)),
    "30j": ("30 derniers jours", timedelta(days=30)),
    "90j": ("90 derniers jours", timedelta(days=90)),
}
PERIODE_PAR_DEFAUT = "7j"
COLONNES_TABLEAU = (
    "emis",
    "livres",
    "echecs",
    "sans_accuse",
    "refuses",
    "latence_cumulee_ms",
    "latences",
)


@dataclass(frozen=True)
class Reseau:
    operateur: str
    emis: int
    # Part du volume émis, en pourcentage entier.
    part: int


@dataclass(frozen=True)
class TableauDeBord:
    periode: str
    libelle: str
    debut: datetime
    fin: datetime
    emis: int
    livres: int
    echecs: int
    sans_accuse: int
    refuses: int
    # Livrés sur les messages dont le sort est connu (livrés, non livrés, sans
    # accusé), en pourcentage à une décimale ; None sans aucun sort connu.
    taux_livraison: float | None
    # Moyenne du délai entre soumission et accusé de livraison, en secondes.
    latence_moyenne_s: float | None
    reseaux: list
    desinscriptions: int

    @property
    def vide(self) -> bool:
        """Rien ne s'est passé sur la période : la page le dit, au lieu
        d'aligner des zéros."""
        return not (self.emis or self.refuses or self.desinscriptions)


def periode(code: str | None, maintenant: datetime | None = None):
    """(code, libellé, début, fin) d'une période du tableau de bord ; un code
    inconnu vaut la période par défaut. La fin est l'heure entamée, comprise."""
    if code not in PERIODES:
        code = PERIODE_PAR_DEFAUT
    libelle, duree = PERIODES[code]
    maintenant = maintenant or timezone.now()
    fin = maintenant.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return code, libelle, fin - duree, fin


def tableau_de_bord(
    client_id, code: str | None = None, maintenant: datetime | None = None
) -> TableauDeBord:
    """Les cinq indicateurs du client sur une période : volume émis, taux de
    livraison, latence moyenne, répartition par réseau, désinscriptions.

    Deux requêtes bornées, l'une sur les compteurs pré-agrégés, campagnes et
    envois hors campagne confondus, l'autre sur la liste noire du compte ;
    jamais de lecture de `journal_message`. Le taux et la latence portent sur
    les accusés reçus dans la période, quelle que soit l'heure d'envoi."""
    code, libelle, debut, fin = periode(code, maintenant)
    lignes = list(
        Compteur.objects.filter(client_id=client_id, heure__gte=debut, heure__lt=fin)
        .values("operateur")
        .annotate(**{c: Sum(c) for c in COLONNES_TABLEAU})
        .order_by("operateur")
    )
    totaux = {c: sum(ligne[c] for ligne in lignes) for c in COLONNES_TABLEAU}
    connus = totaux["livres"] + totaux["echecs"] + totaux["sans_accuse"]
    emis = totaux["emis"]
    reseaux = sorted(
        (
            Reseau(ligne["operateur"], ligne["emis"], round(100 * ligne["emis"] / emis))
            for ligne in lignes
            if ligne["emis"]
        ),
        key=lambda r: (-r.emis, r.operateur),
    )
    desinscriptions = ListeNoire.objects.filter(
        client_id=client_id,
        origine=ListeNoire.Origine.STOP_SMS,
        ajoute_le__gte=debut,
        ajoute_le__lt=fin,
    ).count()
    return TableauDeBord(
        periode=code,
        libelle=libelle,
        debut=debut,
        fin=fin,
        emis=emis,
        livres=totaux["livres"],
        echecs=totaux["echecs"],
        sans_accuse=totaux["sans_accuse"],
        refuses=totaux["refuses"],
        taux_livraison=round(100 * totaux["livres"] / connus, 1) if connus else None,
        latence_moyenne_s=(
            round(totaux["latence_cumulee_ms"] / totaux["latences"] / 1000, 1)
            if totaux["latences"]
            else None
        ),
        reseaux=reseaux,
        desinscriptions=desinscriptions,
    )
