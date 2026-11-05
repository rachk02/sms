"""Porte d'écriture des campagnes : composition, aperçu, envoi de test et
confirmation (T-61).

L'aperçu recalcule tout à chaque affichage : destinataires après liste noire,
expéditeur retenu réseau par réseau selon la politique de la campagne, segments
et coût au cas le plus long. La confirmation refait ce calcul et le compare aux
chiffres que le client a lus : s'ils ont changé entretemps (un import, une
désinscription), elle refuse, pour que le client ne confirme jamais d'autres
chiffres que ceux qu'il a vus. Confirmée, la campagne fige ses destinataires
et réserve son coût en une fois (décision F-15).
"""

from collections import Counter
from dataclasses import dataclass, field

from django.db import connection, transaction
from django.db.models import Q, Value
from django.db.models.functions import Coalesce, NullIf
from django.utils import timezone

from apps.contacts import liste_noire, segments
from apps.contacts.models import Contact, Liste, ListeContact, Segment
from apps.core.audit import auditer
from apps.core.numeros import NumeroInvalide, normaliser
from apps.credits import services as credits
from apps.credits.tarifs import prix_segment
from apps.expediteurs.controle import Action, Choix, choisir
from apps.expediteurs.models import OPERATEURS, Expediteur
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
) -> Campagne:
    nom = nom.strip()
    if not nom:
        raise CampagneRefusee("Une campagne porte un nom.")
    try:
        modeles.gabarit(client, texte)
    except ModeleInvalide as refus:
        raise CampagneRefusee(str(refus)) from None
    expediteur = None
    if expediteur_id:
        expediteur = Expediteur.objects.filter(client=client, id=expediteur_id).first()
        if expediteur is None:
            raise CampagneRefusee("Identifiant d'expéditeur introuvable pour ce compte.")
    if politique not in Campagne.Politique.values or type_ not in Campagne.Type.values:
        raise CampagneRefusee("Politique ou type de campagne inconnu.")
    return Campagne.objects.create(
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

    @property
    def cout(self) -> int:
        return self.estimation.cout

    @property
    def suffisant(self) -> bool:
        return self.disponible >= self.cout

    @property
    def manque(self) -> int:
        return max(0, self.cout - self.disponible)


def apercu(campagne: Campagne) -> Apercu:
    client = campagne.client
    vises = contacts(campagne)
    par_reseau = Counter(vises.annotate(reseau=RESEAU).values_list("reseau", flat=True))
    choix = _choix_par_reseau(campagne, par_reseau)
    servis = [r for r, c in choix.items() if c.action != Action.REFUSER]
    condition = Q(operateur_confirme__in=servis) | Q(
        operateur_confirme="", operateur_presume__in=servis
    )
    gabarit = modeles.gabarit(client, campagne.modele_texte)
    estimation = estimer(client, gabarit, vises.filter(condition))
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
    )


# Envoi de test


def envoyer_test(campagne: Campagne, *, numero: str, auteur) -> object:
    """Un exemplaire du message, au cas le plus long, vers un numéro. Facturé
    comme un envoi unitaire, hors de la réservation de la campagne (décision
    du 30 septembre 2026) ; toujours sur la file prioritaire."""
    client = campagne.client
    try:
        numero = normaliser(numero, client.pays)
    except NumeroInvalide as refus:
        raise CampagneRefusee(f"Numéro de test invalide : {refus.cause}.") from None
    reseau = classer(numero, client.pays)
    if not reseau:
        raise CampagneRefusee("Réseau du numéro de test inconnu.")
    gabarit = modeles.gabarit(client, campagne.modele_texte)
    exemple = estimer(client, gabarit, contacts(campagne)).exemple
    texte = exemple or gabarit.rendre(Contact(numero_normalise=numero, champs={}))
    choix = choisir(client.id, _libelle(campagne), reseau, campagne.politique_expediteur)
    expediteur = choix.expediteur if choix.action == Action.ENVOYER else numero_court()
    prix = prix_segment(client.id, reseau)
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
            )
        except credits.SoldeInsuffisant as refus:
            raise CampagneRefusee(
                f"Solde insuffisant pour l'envoi de test : {refus.disponible} FCFA disponibles."
            ) from None
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


@transaction.atomic
def confirmer(campagne: Campagne, *, auteur, destinataires_lus: int, cout_lu: int) -> Campagne:
    """Fige les destinataires et réserve le coût annoncé, en une transaction."""
    campagne = Campagne.objects.select_for_update().get(id=campagne.id)
    if campagne.etat != Campagne.Etat.BROUILLON:
        raise CampagneRefusee("Cette campagne est déjà confirmée.")
    vu = apercu(campagne)
    if (vu.destinataires, vu.cout) != (destinataires_lus, cout_lu):
        raise ConfirmationPerimee(
            f"Les chiffres ont changé depuis votre lecture : {vu.destinataires} destinataires "
            f"pour {vu.cout} FCFA. Vérifiez l'aperçu et confirmez de nouveau."
        )
    if vu.destinataires == 0:
        raise CampagneRefusee("Aucun destinataire à servir.")
    try:
        credits.reserver(campagne.client_id, vu.cout, campagne_id=campagne.id)
    except credits.SoldeInsuffisant as refus:
        raise CampagneRefusee(
            f"Solde insuffisant : {refus.disponible} FCFA disponibles, {refus.demande} nécessaires."
        ) from None
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
    campagne.etat = Campagne.Etat.EN_ATTENTE
    campagne.destinataires_prevus = vu.destinataires
    campagne.cout_estime = vu.cout
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
