"""Porte d'écriture des campagnes : composition, aperçu, envoi de test et
confirmation (T-61).

L'aperçu recalcule tout à chaque affichage : destinataires après liste noire,
expéditeur retenu réseau par réseau selon la politique de la campagne, segments
et coût au cas le plus long. La confirmation refait ce calcul et le compare aux
chiffres que le client a lus : s'ils ont changé entretemps (un import, une
désinscription), elle refuse, pour que le client ne confirme jamais d'autres
chiffres que ceux qu'il a vus. Confirmée, la campagne fige ses destinataires
et réserve son coût en une fois (décision F-15).

Planification et récurrence (T-65). Une campagne programmée est figée et
réservée à sa confirmation, puis attend son heure (PLANIFIEE). Une campagne
récurrente est un patron : sa confirmation enregistre la fréquence et le
plafond, sans rien figer ni réserver ; chaque occurrence est une campagne
fille, figée et réservée à son heure par la même fonction que la confirmation
d'un client.
"""

import math
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.db.models import Q, Value
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone

from apps.comptes.models import Client
from apps.conformite import fraude
from apps.conformite.antispam import TexteProscrit, exiger_admis
from apps.conformite.models import RegleFraude
from apps.contacts import liste_noire, segments
from apps.contacts.models import Contact, Liste, ListeContact, Segment
from apps.core.audit import auditer
from apps.core.numeros import NumeroInvalide, normaliser
from apps.credits import services as credits
from apps.credits.tarifs import AucunTarif, prix_segment, tarif
from apps.expediteurs.controle import Action, Choix, choisir
from apps.expediteurs.models import OPERATEURS, Expediteur
from apps.journal.selectors import volume_mensuel
from apps.journal.services import enregistrer_message, mettre_en_file
from apps.telecom.numero_court import numero_court
from apps.telecom.prefixes import classer

from . import modeles
from .estimation import Estimation, estimer
from .models import Campagne, Destinataire
from .personnalisation import ModeleInvalide


class CampagneRefusee(ValueError):
    pass


class ConfirmationPerimee(CampagneRefusee):
    """Les chiffres ont changé depuis l'aperçu que le client a lu."""


# Fréquences proposées, en jours : un intervalle fixe, sans calendrier.
RECURRENCES = {1: "Chaque jour", 7: "Chaque semaine", 14: "Toutes les deux semaines"}

# Durées d'étalement proposées, en heures (T-66).
LISSAGES = (1, 2, 3, 4, 6, 8, 12)


def _programmation(
    planifiee_pour: datetime | None,
    recurrence_jours: int | None,
    recurrence_fin: datetime | None,
    plafond: int | None,
) -> dict:
    """Valide l'heure d'envoi et la récurrence d'une campagne."""
    maintenant = timezone.now()
    if planifiee_pour is not None and planifiee_pour <= maintenant:
        raise CampagneRefusee("L'heure d'envoi programmée est déjà passée.")
    if recurrence_jours is None:
        return {"planifiee_pour": planifiee_pour}
    if recurrence_jours not in RECURRENCES:
        raise CampagneRefusee("Fréquence de récurrence inconnue.")
    if not isinstance(plafond, int) or isinstance(plafond, bool) or plafond <= 0:
        raise CampagneRefusee(
            "Une campagne récurrente porte un plafond de coût par envoi, en FCFA entiers."
        )
    debut = planifiee_pour or maintenant
    if recurrence_fin is not None and recurrence_fin <= debut:
        raise CampagneRefusee("La fin de la récurrence précède son premier envoi.")
    return {
        "planifiee_pour": planifiee_pour,
        "recurrence_jours": recurrence_jours,
        "recurrence_fin": recurrence_fin,
        "plafond_occurrence": plafond,
    }


# Composition


def _cible(client, listes: list, segment_id) -> dict:
    listes = [str(i) for i in listes if i]
    if Liste.objects.filter(client=client, id__in=listes).count() != len(set(listes)):
        raise CampagneRefusee("Liste introuvable pour ce compte.")
    if segment_id:
        segment = Segment.objects.filter(client=client, id=segment_id).first()
        if segment is None:
            raise CampagneRefusee("Segment introuvable pour ce compte.")
        if not segment.actif:
            raise CampagneRefusee(segment.motif_desactivation or "Ce segment est désactivé.")
    if not listes and not segment_id:
        raise CampagneRefusee("Choisissez au moins une liste ou un segment.")
    return {"listes": sorted(set(listes)), "segment_id": str(segment_id) if segment_id else None}


@transaction.atomic
def creer(
    *,
    client,
    auteur,
    nom: str,
    texte: str,
    listes=(),
    segment_id=None,
    expediteur_id=None,
    politique: str = Campagne.Politique.REPLI,
    type_: str = Campagne.Type.MARKETING,
    planifiee_pour: datetime | None = None,
    recurrence_jours: int | None = None,
    recurrence_fin: datetime | None = None,
    plafond: int | None = None,
    lissage_heures: int | None = None,
) -> Campagne:
    nom = nom.strip()
    if lissage_heures is not None and lissage_heures not in LISSAGES:
        raise CampagneRefusee("Durée d'étalement inconnue.")
    if not nom:
        raise CampagneRefusee("Une campagne porte un nom.")
    try:
        modeles.gabarit(client, texte)
    except ModeleInvalide as refus:
        raise CampagneRefusee(str(refus)) from None
    _texte_admis(texte)
    expediteur = None
    if expediteur_id:
        expediteur = Expediteur.objects.filter(client=client, id=expediteur_id).first()
        if expediteur is None:
            raise CampagneRefusee("Identifiant d'expéditeur introuvable pour ce compte.")
    if politique not in Campagne.Politique.values or type_ not in Campagne.Type.values:
        raise CampagneRefusee("Politique ou type de campagne inconnu.")
    programmation = _programmation(planifiee_pour, recurrence_jours, recurrence_fin, plafond)
    return Campagne.objects.create(
        **programmation,
        lissage_duree_minutes=lissage_heures * 60 if lissage_heures else None,
        client=client,
        nom=nom,
        type=type_,
        modele_texte=texte,
        cible=_cible(client, list(listes), segment_id),
        expediteur=expediteur,
        politique_expediteur=politique,
        creee_par=auteur,
    )


# Destinataires et aperçu


# Le réseau d'un contact : l'opérateur confirmé par un accusé, sinon le présumé.
RESEAU = Coalesce(NullIf("operateur_confirme", Value("")), "operateur_presume")


def _operateur(prefixe: str = "") -> str:
    return f"COALESCE(NULLIF({prefixe}operateur_confirme, ''), {prefixe}operateur_presume)"


def contacts(campagne: Campagne):
    """Les contacts visés, actifs et hors liste noire, chacun une seule fois même
    présent dans plusieurs listes (décision F-13)."""
    client = campagne.client
    condition = Q(pk__in=[])
    if campagne.cible.get("listes"):
        membres = ListeContact.objects.filter(
            client_id=client.id, liste_id__in=campagne.cible["listes"]
        ).values("contact_id")
        condition |= Q(id__in=membres)
    if campagne.cible.get("segment_id"):
        segment = Segment.objects.get(client=client, id=campagne.cible["segment_id"])
        condition |= Q(id__in=segments.filtrer(client, segment.criteres).values("id"))
    return liste_noire.destinataires(client.id, Contact.objects.filter(condition, client=client))


def _libelle(campagne: Campagne) -> str:
    return campagne.expediteur.libelle if campagne.expediteur else numero_court()


def _choix_par_reseau(campagne: Campagne, reseaux) -> dict:
    """L'expéditeur retenu par réseau. Un contact dont le réseau est inconnu
    (préfixe absent de la table) ne peut être acheminé : il n'est pas servi."""
    libelle, politique = _libelle(campagne), campagne.politique_expediteur
    return {
        reseau: choisir(campagne.client_id, libelle, reseau, politique)
        if reseau
        else Choix(Action.REFUSER, "")
        for reseau in reseaux
    }


def _texte_admis(texte: str) -> list:
    """Dictionnaire anti-spam (T-99) : une expression bloquante refuse la
    campagne et se nomme ; rend les avertissements."""
    try:
        return exiger_admis(texte)
    except TexteProscrit as refus:
        raise CampagneRefusee(str(refus)) from None


@dataclass(frozen=True)
class Apercu:
    destinataires: int
    refuses: int
    # Contacts au réseau inconnu, non servis.
    inconnus: int
    par_reseau: dict
    # (action, expéditeur) -> nombre de destinataires
    par_expediteur: dict
    estimation: Estimation
    disponible: int
    choix: dict = field(repr=False)
    # Ce que `_figer` copie sur la campagne : voir Campagne.tarification.
    tarification: dict = field(default_factory=dict, repr=False)
    # Expressions du dictionnaire anti-spam qui avertissent sans bloquer.
    avertissements: list = field(default_factory=list)

    @property
    def cout(self) -> int:
        return self.estimation.cout

    @property
    def suffisant(self) -> bool:
        return self.disponible >= self.cout

    @property
    def manque(self) -> int:
        return max(0, self.cout - self.disponible)


def tarifer(client, reseaux, maintenant: datetime | None = None) -> dict:
    """Le tarif d'une campagne lancée maintenant : la version de grille en
    vigueur et, par réseau servi, le prix d'un segment au palier atteint par le
    volume du mois. Figé sur la campagne à sa confirmation, il vaut pour tous
    ses messages, même si un palier est franchi ou la grille changée pendant
    la diffusion (décision du 6 novembre 2026). Lève AucunTarif."""
    maintenant = maintenant or timezone.now()
    volume = volume_mensuel(client.id, maintenant)
    detail = {
        reseau: tarif(client.id, reseau, a=maintenant, volume=volume) for reseau in sorted(reseaux)
    }
    versions = {t.version_id for t in detail.values()}
    return {
        "le": maintenant.isoformat(),
        "version": str(versions.pop()) if len(versions) == 1 and None not in versions else None,
        "volume_mensuel": volume,
        "reseaux": {
            r: {
                "prix": t.prix,
                "palier": t.palier,
                "grille": t.nature,
                "version": str(t.version_id) if t.version_id else None,
            }
            for r, t in detail.items()
        },
    }


def apercu(campagne: Campagne) -> Apercu:
    client = campagne.client
    vises = contacts(campagne)
    par_reseau = Counter(vises.annotate(reseau=RESEAU).values_list("reseau", flat=True))
    choix = _choix_par_reseau(campagne, par_reseau)
    servis = [r for r, c in choix.items() if c.action != Action.REFUSER]
    condition = Q(operateur_confirme__in=servis) | Q(
        operateur_confirme="", operateur_presume__in=servis
    )
    avertissements = _texte_admis(campagne.modele_texte)
    gabarit = modeles.gabarit(client, campagne.modele_texte)
    try:
        tarification = tarifer(client, servis)
        prix = {reseau: t["prix"] for reseau, t in tarification["reseaux"].items()}
        estimation = estimer(client, gabarit, vises.filter(condition), prix)
    except AucunTarif as refus:
        raise CampagneRefusee(str(refus)) from None
    par_expediteur = Counter()
    for reseau, nombre in par_reseau.items():
        if reseau:
            par_expediteur[(choix[reseau].action, choix[reseau].expediteur)] += nombre
    return Apercu(
        destinataires=estimation.destinataires,
        refuses=sum(n for r, n in par_reseau.items() if r and r not in servis),
        inconnus=par_reseau.get("", 0),
        par_reseau={r: n for r, n in par_reseau.items() if r},
        par_expediteur=dict(par_expediteur),
        estimation=estimation,
        disponible=credits.solde(client.id).disponible,
        choix=choix,
        tarification=tarification,
        avertissements=avertissements,
    )


# Envoi de test


def _compte_ouvert(client) -> None:
    """Un compte suspendu ou clos n'envoie plus rien (T-89)."""
    if client.etat != Client.Etat.ACTIF:
        raise CampagneRefusee(f"Compte {client.get_etat_display().lower()} : aucun envoi possible.")


def envoyer_test(campagne: Campagne, *, numero: str, auteur) -> object:
    """Un exemplaire du message, au cas le plus long, vers un numéro. Facturé
    comme un envoi unitaire, hors de la réservation de la campagne (décision
    du 30 septembre 2026) ; toujours sur la file prioritaire."""
    client = campagne.client
    _compte_ouvert(client)
    try:
        numero = normaliser(numero, client.pays)
    except NumeroInvalide as refus:
        raise CampagneRefusee(f"Numéro de test invalide : {refus.cause}.") from None
    reseau = classer(numero, client.pays)
    if not reseau:
        raise CampagneRefusee("Réseau du numéro de test inconnu.")
    _texte_admis(campagne.modele_texte)
    gabarit = modeles.gabarit(client, campagne.modele_texte)
    choix = choisir(client.id, _libelle(campagne), reseau, campagne.politique_expediteur)
    expediteur = choix.expediteur if choix.action == Action.ENVOYER else numero_court()
    try:
        exemple = estimer(client, gabarit, contacts(campagne)).exemple
        prix = prix_segment(client.id, reseau, volume=volume_mensuel(client.id))
    except AucunTarif as refus:
        raise CampagneRefusee(str(refus)) from None
    texte = exemple or gabarit.rendre(Contact(numero_normalise=numero, champs={}))
    with transaction.atomic():
        message = enregistrer_message(
            client=client,
            numero_normalise=numero,
            operateur=reseau,
            expediteur_libelle=expediteur,
            texte=texte,
            cout_unitaire=prix,
            cle_idempotence=f"test:{campagne.id}:{timezone.now().timestamp()}",
        )
        try:
            credits.reserver(
                client.id,
                message.cout_total,
                message_id=message.id,
                message_date_reception=message.date_reception,
                attente_max_ms=credits.ATTENTE_RESERVATION_MS,
            )
        except credits.SoldeInsuffisant as refus:
            raise CampagneRefusee(
                f"Solde insuffisant pour l'envoi de test : {refus.disponible} FCFA disponibles."
            ) from None
        except credits.SoldeOccupe as refus:
            raise CampagneRefusee(str(refus)) from None
        mettre_en_file(message, prioritaire=True)
    return message


# Confirmation


FIGER = """
INSERT INTO campagnes_destinataire
    (campagne_id, contact_id, client_id, numero_normalise, operateur, expediteur, action)
SELECT %s, c.id, c.client_id, c.numero_normalise, {reseau}, %s, %s
FROM ({requete}) AS c
WHERE {reseau} = %s
ON CONFLICT DO NOTHING
"""


def _figer(campagne: Campagne, vu: Apercu, *, auteur, attente_max_ms=None) -> Campagne:
    """Fige les destinataires et réserve le coût de l'aperçu `vu`, pour une
    campagne verrouillée par l'appelant. Seule porte, que la confirmation
    vienne d'un client ou d'une occurrence sous son plafond. Une campagne
    programmée attend ensuite son heure ; les autres partent aussitôt.
    `attente_max_ms` borne l'attente du verrou du solde pour un client qui
    attend la réponse ; l'ordonnanceur attend sans borne."""
    _compte_ouvert(campagne.client)
    if vu.destinataires == 0:
        raise CampagneRefusee("Aucun destinataire à servir.")
    requete, parametres = (
        contacts(campagne)
        .values("id", "client_id", "numero_normalise", "operateur_confirme", "operateur_presume")
        .query.sql_with_params()
    )
    with connection.cursor() as curseur:
        for reseau, choix in vu.choix.items():
            if choix.action == Action.REFUSER:
                continue
            curseur.execute(
                FIGER.format(reseau=_operateur("c."), requete=requete),
                [campagne.id, choix.expediteur, str(choix.action), *parametres, reseau],
            )
    figes = Destinataire.objects.filter(campagne=campagne).count()
    if figes != vu.destinataires:
        raise RuntimeError(f"{figes} destinataires figés pour {vu.destinataires} annoncés.")
    # La réservation vient après l'insertion des destinataires : le verrou du
    # solde n'est tenu que pour la fin de la transaction, pas pendant l'insertion.
    # Un refus annule la transaction entière, destinataires compris.
    try:
        credits.reserver(
            campagne.client_id, vu.cout, campagne_id=campagne.id, attente_max_ms=attente_max_ms
        )
    except credits.SoldeInsuffisant as refus:
        raise CampagneRefusee(
            f"Solde insuffisant : {refus.disponible} FCFA disponibles, {refus.demande} nécessaires."
        ) from None
    except credits.SoldeOccupe as refus:
        raise CampagneRefusee(str(refus)) from None
    campagne.etat = Campagne.Etat.PLANIFIEE if campagne.planifiee_pour else Campagne.Etat.EN_ATTENTE
    if campagne.lissage_duree_minutes:
        # Arrondi au-dessus : la campagne finit au plus tard à la fin de la durée.
        campagne.lissage_par_minute = math.ceil(vu.destinataires / campagne.lissage_duree_minutes)
    campagne.destinataires_prevus = vu.destinataires
    campagne.cout_estime = vu.cout
    campagne.tarification = vu.tarification
    campagne.segments_prevus = vu.estimation.segments
    campagne.encodage_prevu = str(vu.estimation.encodage)
    campagne.confirmee_le = timezone.now()
    campagne.replis = modeles.replis_declares(campagne.client)
    campagne.save()
    auditer(
        "campagnes.confirmation",
        campagne,
        auteur=auteur,
        client=campagne.client,
        apres={"destinataires": vu.destinataires, "cout": vu.cout},
    )
    return campagne


def _armer(campagne: Campagne, vu: Apercu, *, auteur) -> Campagne:
    """Confirme une campagne récurrente : fréquence et plafond enregistrés,
    première occurrence à l'heure choisie ou au prochain tour de
    l'ordonnanceur. Rien n'est figé ni réservé ici."""
    campagne.etat = Campagne.Etat.PLANIFIEE
    campagne.prochaine_occurrence = campagne.planifiee_pour or timezone.now()
    campagne.confirmee_le = timezone.now()
    campagne.save()
    auditer(
        "campagnes.recurrence",
        campagne,
        auteur=auteur,
        client=campagne.client,
        apres={
            "tous_les_jours": campagne.recurrence_jours,
            "plafond": campagne.plafond_occurrence,
            "premiere": campagne.prochaine_occurrence.isoformat(),
            "cout_lu": vu.cout,
        },
    )
    return campagne


# La plus longue série de numéros consécutifs d'une cible (T-100) : les
# numéros, sans leur +, rangés ; une série garde le même écart entre le numéro
# et son rang.
CONTIGUS = r"""
SELECT count(*) AS longueur, min(v) AS premier FROM (
    SELECT v, v - row_number() OVER (ORDER BY v) AS serie FROM (
        SELECT DISTINCT substr(c.numero_normalise, 2)::bigint AS v
        FROM ({requete}) AS c
        WHERE c.numero_normalise ~ '^\+[0-9]{{6,15}}$'
    ) AS numeros
) AS rangs
GROUP BY serie ORDER BY longueur DESC LIMIT 1
"""


def surveiller_contigus(campagne: Campagne) -> None:
    """Numéros contigus (T-100). Au seuil d'alerte, l'exploitation est
    prévenue et la campagne part ; au seuil critique, elle est refusée.
    L'alerte s'écrit dans son propre point de sauvegarde, avant le refus : elle
    reste même quand la confirmation échoue."""
    regle = fraude.regle(RegleFraude.Code.CONTIGUS)
    if regle is None:
        return
    requete, parametres = contacts(campagne).values("numero_normalise").query.sql_with_params()
    with connection.cursor() as curseur:
        curseur.execute(CONTIGUS.format(requete=requete), parametres)
        ligne = curseur.fetchone()
    if ligne is None:
        return
    longueur, premier = ligne
    gravite = fraude.gravite(longueur, regle)
    if gravite is None:
        return
    serie = {"premier": f"+{premier}", "dernier": f"+{premier + longueur - 1}"}
    fraude.signaler(
        campagne.client_id,
        RegleFraude.Code.CONTIGUS,
        gravite=gravite,
        valeur=longueur,
        campagne_id=campagne.id,
        details={**serie, "campagne": campagne.nom},
    )
    if gravite == fraude.AlerteFraude.Gravite.CRITIQUE:
        raise CampagneRefusee(
            f"Envoi refusé : {longueur} numéros consécutifs, de {serie['premier']} à "
            f"{serie['dernier']}. L'exploitation est prévenue."
        )


def confirmer(campagne: Campagne, *, auteur, destinataires_lus: int, cout_lu: int) -> Campagne:
    """Confirmation par le client des chiffres qu'il a lus : fige et réserve,
    ou, pour une campagne récurrente, arme sa récurrence. Les numéros
    contigus se vérifient avant, hors de la transaction de la confirmation,
    pour que l'alerte survive au refus ; chaque occurrence d'une récurrence
    est vérifiée à son tour."""
    if not campagne.recurrente and campagne.etat == Campagne.Etat.BROUILLON:
        surveiller_contigus(campagne)
    return _confirmer(campagne, auteur=auteur, destinataires_lus=destinataires_lus, cout_lu=cout_lu)


@transaction.atomic
def _confirmer(campagne: Campagne, *, auteur, destinataires_lus: int, cout_lu: int) -> Campagne:
    campagne = Campagne.objects.select_for_update().get(id=campagne.id)
    if campagne.etat != Campagne.Etat.BROUILLON:
        raise CampagneRefusee("Cette campagne est déjà confirmée.")
    if campagne.planifiee_pour and campagne.planifiee_pour <= timezone.now():
        raise CampagneRefusee(
            "L'heure d'envoi programmée est passée depuis l'enregistrement : "
            "créez la campagne de nouveau avec une autre heure."
        )
    vu = apercu(campagne)
    if (vu.destinataires, vu.cout) != (destinataires_lus, cout_lu):
        raise ConfirmationPerimee(
            f"Les chiffres ont changé depuis votre lecture : {vu.destinataires} destinataires "
            f"pour {vu.cout} FCFA. Vérifiez l'aperçu et confirmez de nouveau."
        )
    if campagne.recurrente:
        return _armer(campagne, vu, auteur=auteur)
    return _figer(campagne, vu, auteur=auteur, attente_max_ms=credits.ATTENTE_RESERVATION_MS)


# Occurrences d'une campagne récurrente


def _nom_occurrence(patron: Campagne, creneau: datetime) -> str:
    date = timezone.localtime(creneau).strftime("%d/%m/%Y %H:%M")
    return f"{patron.nom[: 150 - len(date) - 2]}, {date}"


def creer_occurrence(patron: Campagne, creneau: datetime) -> tuple[Campagne, bool]:
    """L'occurrence d'un créneau, créée une seule fois : la contrainte d'unicité
    (parent, créneau) arrête un second ordonnanceur ou un rejeu. À appeler
    dans une transaction, patron verrouillé."""
    return Campagne.objects.get_or_create(
        parent=patron,
        occurrence_le=creneau,
        defaults={
            "client": patron.client,
            "nom": _nom_occurrence(patron, creneau),
            "type": patron.type,
            "expediteur": patron.expediteur,
            "modele_texte": patron.modele_texte,
            "cible": patron.cible,
            "politique_expediteur": patron.politique_expediteur,
            "lissage_duree_minutes": patron.lissage_duree_minutes,
            "creee_par": patron.creee_par,
        },
    )


def confirmer_occurrence(occurrence: Campagne, plafond: int) -> str | None:
    """Fige et réserve une occurrence si son coût reste sous le plafond confirmé
    pour sa campagne récurrente (décision F-22). Sinon elle reste en brouillon,
    à confirmer par le client, et la raison est rendue. Le segment est recalculé
    ici, à l'heure de l'occurrence."""
    occurrence = Campagne.objects.select_for_update().get(id=occurrence.id)
    if occurrence.etat != Campagne.Etat.BROUILLON:
        return None
    try:
        _texte_admis(occurrence.modele_texte)
    except CampagneRefusee as refus:
        return str(refus)
    try:
        vu = apercu(occurrence)
    except (Segment.DoesNotExist, CampagneRefusee) as refus:
        return f"Cible introuvable : {refus}"
    if vu.cout > plafond:
        return f"Coût de {vu.cout} FCFA au-dessus du plafond de {plafond} FCFA."
    try:
        surveiller_contigus(occurrence)
    except CampagneRefusee as refus:
        return str(refus)
    try:
        with transaction.atomic():
            _figer(occurrence, vu, auteur=None)
    except CampagneRefusee as refus:
        return str(refus)
    return None


def prochain_creneau(patron: Campagne, maintenant: datetime) -> tuple[datetime, int]:
    """Le dernier créneau échu d'une campagne récurrente et le nombre de
    créneaux sautés : après un arrêt de l'ordonnanceur, une seule occurrence
    part, jamais une rafale de rattrapage."""
    pas = timedelta(days=patron.recurrence_jours)
    creneau, sautes = patron.prochaine_occurrence, 0
    while creneau + pas <= maintenant:
        creneau += pas
        sautes += 1
    return creneau, sautes


# Pause, reprise et annulation (T-64)

PAUSABLES = (Campagne.Etat.EN_ATTENTE, Campagne.Etat.EN_COURS)
ANNULABLES = (
    Campagne.Etat.PLANIFIEE,
    Campagne.Etat.EN_ATTENTE,
    Campagne.Etat.EN_COURS,
    Campagne.Etat.EN_PAUSE,
)


def _verrouillee(campagne: Campagne) -> Campagne:
    # Le même verrou que la publication d'un lot : un lot en cours s'achève
    # avant que l'état change.
    return (
        Campagne.objects.select_for_update(of=("self",))
        .select_related("client")
        .get(id=campagne.id)
    )


@transaction.atomic
def mettre_en_pause(campagne: Campagne, *, auteur) -> Campagne:
    """Arrête la publication et la soumission. Les messages déjà en file sont
    retenus par le worker avant tout créneau ; ceux dont la soumission a
    commencé finissent leur chemin."""
    campagne = _verrouillee(campagne)
    if campagne.etat not in PAUSABLES:
        raise CampagneRefusee(
            f"Une campagne {campagne.get_etat_display().lower()} ne se met pas en pause."
        )
    avant = campagne.etat
    campagne.etat = Campagne.Etat.EN_PAUSE
    campagne.save(update_fields=["etat", "modifie_le"])
    auditer(
        "campagnes.pause",
        campagne,
        auteur=auteur,
        client=campagne.client,
        avant={"etat": avant},
        apres={"etat": campagne.etat},
    )
    return campagne


@transaction.atomic
def reprendre(campagne: Campagne, *, auteur) -> Campagne:
    """Reprend une campagne en pause : l'ordonnanceur remet en file les
    messages retenus, puis publie la suite."""
    campagne = _verrouillee(campagne)
    if campagne.etat != Campagne.Etat.EN_PAUSE:
        raise CampagneRefusee("Seule une campagne en pause se reprend.")
    _compte_ouvert(campagne.client)
    campagne.etat = Campagne.Etat.EN_COURS if campagne.demarree_le else Campagne.Etat.EN_ATTENTE
    campagne.save(update_fields=["etat", "modifie_le"])
    auditer(
        "campagnes.reprise",
        campagne,
        auteur=auteur,
        client=campagne.client,
        avant={"etat": Campagne.Etat.EN_PAUSE},
        apres={"etat": campagne.etat},
    )
    return campagne


@transaction.atomic
def annuler(campagne: Campagne, *, auteur) -> Campagne:
    """Annule ce qui n'est pas encore parti. Les messages déjà soumis au réseau
    ne se rattrapent pas et restent facturés ; les autres sont annulés par
    lots par l'ordonnanceur, puis le reliquat de la réservation est libéré.
    Une campagne qui n'a encore rien publié est close sur-le-champ."""
    from .diffusion import clore

    campagne = _verrouillee(campagne)
    if campagne.etat not in ANNULABLES:
        raise CampagneRefusee(
            f"Une campagne {campagne.get_etat_display().lower()} ne s'annule plus."
        )
    avant = campagne.etat
    campagne.etat = Campagne.Etat.ANNULEE
    campagne.save(update_fields=["etat", "modifie_le"])
    auditer(
        "campagnes.annulation",
        campagne,
        auteur=auteur,
        client=campagne.client,
        avant={"etat": avant},
        apres={"etat": campagne.etat},
    )
    if not campagne.demarree_le:
        clore(campagne)
    return campagne


# Pour l'affichage : les réseaux dans l'ordre habituel.
RESEAUX = OPERATEURS


# Envois unitaires de l'API (T-108)

# Un lot se borne : au-delà, l'intégrateur découpe.
LOT_MAXIMUM = 1000
SEGMENTS_MAXIMUM = 10


@dataclass
class Envoi:
    """Le sort d'une demande d'envoi : un message accepté, ou un refus nommé."""

    rang: int
    message: object = None
    code: str = ""
    motif: str = ""

    @property
    def accepte(self) -> bool:
        return self.message is not None


def accepter_unitaires(client, demandes: list[dict], *, cle_api: str = "") -> list[Envoi]:
    """Accepte des envois hors campagne : chacun est écrit au journal, son coût
    réservé, puis il est déposé dans la file prioritaire de son réseau. C'est
    la porte des envois de l'API, unitaires comme par lot.

    Une demande invalide n'annule pas les autres : chacune reçoit son sort.
    Le contrôle qui ne dépend que de la demande se fait d'abord, hors de tout
    verrou ; viennent ensuite le plafond du compte (les premières passent),
    puis la réservation groupée du crédit (les premières passent tant que le
    disponible suffit). La liste noire, le bac à sable et l'état de
    l'expéditeur au moment de partir restent contrôlés par le moteur d'envoi,
    comme pour tout message.

    Chaque demande porte `numero`, `texte` et, facultatif, `expediteur` (le
    numéro court par défaut). À appeler dans une transaction."""
    from redis.exceptions import RedisError

    from apps.conformite.plafonds import consommer_lot
    from apps.core.encodage import analyser
    from apps.expediteurs.controle import couvert
    from apps.journal.services import enregistrer_messages, mettre_en_file_lot
    from apps.telecom.prefixes import table

    _compte_ouvert(client)
    if len(demandes) > LOT_MAXIMUM:
        raise CampagneRefusee(f"Un lot compte au plus {LOT_MAXIMUM} messages.")
    sorts = [Envoi(rang) for rang in range(len(demandes))]
    prefixes = table(client.pays)
    volume = volume_mensuel(client.id)
    prix, couverts, valides = {}, {}, []
    for envoi, demande in zip(sorts, demandes, strict=True):
        texte = demande.get("texte") or ""
        libelle = (demande.get("expediteur") or numero_court()).strip()
        try:
            numero = normaliser(str(demande.get("numero") or ""), client.pays)
        except NumeroInvalide as refus:
            envoi.code, envoi.motif = "NUMERO_INVALIDE", f"Numéro invalide : {refus.cause}."
            continue
        reseau = classer(numero, client.pays, prefixes)
        if not reseau:
            envoi.code, envoi.motif = "RESEAU_INCONNU", "Réseau du numéro inconnu."
            continue
        if not texte.strip():
            envoi.code, envoi.motif = "TEXTE_VIDE", "Le texte est vide."
            continue
        segments = analyser(texte).segments
        if segments > SEGMENTS_MAXIMUM:
            envoi.code = "TEXTE_TROP_LONG"
            envoi.motif = f"Le texte dépasse {SEGMENTS_MAXIMUM} segments."
            continue
        if (libelle, reseau) not in couverts:
            couverts[libelle, reseau] = couvert(client.id, libelle, reseau)
        if not couverts[libelle, reseau]:
            envoi.code = "EXPEDITEUR_NON_AUTORISE"
            envoi.motif = f"L'expéditeur {libelle} n'est pas approuvé sur le réseau {reseau}."
            continue
        if reseau not in prix:
            try:
                prix[reseau] = prix_segment(client.id, reseau, volume=volume)
            except AucunTarif:
                prix[reseau] = None
        if prix[reseau] is None:
            envoi.code, envoi.motif = "TARIF_ABSENT", f"Aucun tarif pour le réseau {reseau}."
            continue
        valides.append(
            (
                envoi,
                {
                    "id": uuid.uuid7(),
                    "numero_normalise": numero,
                    "operateur": reseau,
                    "expediteur_libelle": libelle,
                    "texte": texte,
                    "cout_unitaire": prix[reseau],
                    "date_reception": timezone.now(),
                },
                prix[reseau] * segments,
            )
        )
    # Plafond horaire et journalier du compte, en messages (T-101, T-107).
    try:
        places, periode = consommer_lot(client, len(valides), cle_api=cle_api)
    except RedisError:
        # Mode permissif (T-107) : le crédit borne encore ce qui part.
        places, periode = len(valides), None
    for envoi, _, _ in valides[places:]:
        envoi.code = "PLAFOND_COMPTE"
        envoi.motif = f"Plafond {periode} du compte atteint."
    valides = valides[:places]
    with transaction.atomic():
        reserves = credits.reserver_messages(
            client.id,
            [
                credits.LigneReservation(ligne["id"], ligne["date_reception"], cout)
                for _, ligne, cout in valides
            ],
            attente_max_ms=credits.ATTENTE_RESERVATION_MS,
        )
        for envoi, _, _ in valides[reserves:]:
            envoi.code, envoi.motif = "SOLDE_INSUFFISANT", "Solde insuffisant pour ce message."
        valides = valides[:reserves]
        messages = enregistrer_messages(client=client, lignes=[ligne for _, ligne, _ in valides])
        mettre_en_file_lot(messages, prioritaire=True)
    for (envoi, _, _), message in zip(valides, messages, strict=True):
        envoi.message = message
    return sorts
