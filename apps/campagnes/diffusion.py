"""Découpage d'une campagne confirmée et publication en file (T-62).

L'ordonnanceur, seul exemplaire actif, publie les campagnes par lots. Un lot
est une transaction : il écrit au journal un message par destinataire figé non
encore servi, la ligne de `journal_destinataire` qui interdit tout doublon, et
l'événement de la boîte d'envoi qui dépose le message dans la file de son
réseau. Une campagne rejouée depuis le début ne produit aucun doublon : les
destinataires servis sont écartés, et la clé primaire de `journal_destinataire`
arrête ce qui passerait quand même.

Le texte est rendu au moment de la publication, avec les données du contact à
cet instant. Avant chaque lot, l'expéditeur est réévalué réseau par réseau : un
identifiant révoqué pendant la diffusion suit la politique de la campagne, un
identifiant approuvé entretemps libère les destinataires qui l'attendaient.

Pause, reprise, annulation et clôture (T-64). Une campagne en pause ou annulée
ne publie plus ; ses messages déjà en file sont retenus par le worker avant
tout créneau de débit (`retenue`), en attente pour une pause, annulés pour une
annulation. À la reprise, l'ordonnanceur remet en file les messages retenus,
par lots. Une campagne se clôt quand plus rien n'est à publier ni à soumettre :
les messages soumis qui n'auraient pas encore été réglés le sont, puis le
reliquat de la réservation est libéré. Un message dont la soumission a commencé
n'est jamais annulé : il est peut-être chez l'opérateur, il finit son chemin
et se règle avant la clôture.
"""

import logging
from datetime import datetime, timedelta

from django.db import connection, transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.conformite import fenetre
from apps.contacts.models import Contact
from apps.core import controles
from apps.core.audit import auditer
from apps.core.etats import Etat
from apps.credits import services as credits
from apps.credits.models import Reservation
from apps.credits.tarifs import prix_segment
from apps.expediteurs.controle import Action, choisir
from apps.journal.models import Destinataire as Servi
from apps.journal.models import Evenement
from apps.journal.services import (
    MOTIF_ANNULE,
    NON_SOUMIS,
    changer_etats_campagne,
    enregistrer_message,
    mettre_en_file,
    reste_a_soumettre,
)
from apps.telecom.numero_court import numero_court

from .models import Campagne, Destinataire
from .personnalisation import compiler

journal = logging.getLogger(__name__)

TAILLE_LOT = 500
# Un palier de lissage : la quantité d'une minute, publiée d'un coup (T-66).
PALIER = timedelta(minutes=1)
A_PUBLIER = (Destinataire.Action.ENVOYER, Destinataire.Action.REPLI)


def _reevaluer(campagne: Campagne) -> None:
    """Remet à jour l'expéditeur des destinataires non encore servis."""
    libelle = campagne.expediteur.libelle if campagne.expediteur else numero_court()
    non_servis = Destinataire.objects.filter(campagne=campagne).exclude(
        Exists(Servi.objects.filter(campagne_id=campagne.id, contact_id=OuterRef("contact_id")))
    )
    for reseau in non_servis.values_list("operateur", flat=True).distinct():
        choix = choisir(campagne.client_id, libelle, reseau, campagne.politique_expediteur)
        if choix.action == Action.ENVOYER:
            action, expediteur = Destinataire.Action.ENVOYER, libelle
        elif choix.action == Action.REPLI:
            action, expediteur = Destinataire.Action.REPLI, numero_court()
        else:
            # Refus ou attente décidés pendant la diffusion : ces destinataires
            # attendent ; le client peut annuler la campagne (T-64).
            action, expediteur = Destinataire.Action.ATTENDRE, libelle
        non_servis.filter(operateur=reseau).exclude(action=action, expediteur=expediteur).update(
            action=action, expediteur=expediteur
        )


def _palier_suivant(campagne: Campagne, publies: int, maintenant: datetime) -> datetime:
    """L'heure du palier suivant d'une campagne lissée : chaque message publié
    vaut une fraction de minute. Les paliers s'enchaînent à intervalles
    réguliers ; après une pause ou un arrêt de l'ordonnanceur, le calendrier
    repart de maintenant, sans rafale de rattrapage."""
    repere = campagne.prochain_palier
    if repere is None or maintenant - repere >= PALIER:
        repere = maintenant
    return repere + PALIER * publies / campagne.lissage_par_minute


@transaction.atomic
def publier_lot(campagne_id, taille: int = TAILLE_LOT, maintenant: datetime | None = None) -> int:
    """Publie le lot suivant d'une campagne. Renvoie le nombre de messages
    publiés, zéro quand il ne reste rien à publier pour l'instant. Une campagne
    lissée publie un palier, puis attend le suivant."""
    maintenant = maintenant or timezone.now()
    campagne = (
        Campagne.objects.select_for_update(of=("self",))
        .select_related("client", "expediteur")
        .get(id=campagne_id)
    )
    if campagne.etat not in (Campagne.Etat.EN_ATTENTE, Campagne.Etat.EN_COURS):
        return 0
    if hors_fenetre(campagne, maintenant):
        # Avant tout le reste : une campagne qui attend l'ouverture ne démarre
        # pas la nuit, et ses messages retenus ne sont pas remis en file pour
        # être retenus de nouveau (T-72).
        return 0
    if campagne.etat == Campagne.Etat.EN_ATTENTE:
        campagne.etat, campagne.demarree_le = Campagne.Etat.EN_COURS, maintenant
        campagne.save(update_fields=["etat", "demarree_le", "modifie_le"])
    if campagne.demarree_le:
        repris = _remettre_en_file(campagne, taille)
        if repris:
            return repris
    if campagne.lissage_par_minute:
        if campagne.prochain_palier and maintenant < campagne.prochain_palier:
            return 0
        taille = min(taille, campagne.lissage_par_minute)
    _reevaluer(campagne)
    lot = list(
        Destinataire.objects.filter(campagne=campagne, action__in=A_PUBLIER)
        .exclude(
            Exists(Servi.objects.filter(campagne_id=campagne.id, contact_id=OuterRef("contact_id")))
        )
        .order_by("contact_id")[:taille]
    )
    if not lot:
        return 0
    client = campagne.client
    gabarit = compiler(campagne.modele_texte, campagne.replis)
    contacts = Contact.objects.filter(client=client).in_bulk([d.contact_id for d in lot])
    prioritaire = campagne.type != Campagne.Type.MARKETING
    for destinataire in lot:
        # Un contact supprimé depuis la confirmation reçoit les valeurs de repli.
        contact = contacts.get(destinataire.contact_id) or Contact(
            numero_normalise=destinataire.numero_normalise, champs={}
        )
        message = enregistrer_message(
            client=client,
            numero_normalise=destinataire.numero_normalise,
            operateur=destinataire.operateur,
            expediteur_libelle=destinataire.expediteur,
            texte=gabarit.rendre(contact),
            cout_unitaire=prix_segment(client.id, destinataire.operateur),
            campagne_id=campagne.id,
            contact_id=destinataire.contact_id,
            source=Evenement.Source.WORKER,
            cle_idempotence=f"campagne:{campagne.id}:{destinataire.contact_id}",
        )
        mettre_en_file(message, prioritaire=prioritaire)
    if campagne.lissage_par_minute:
        campagne.prochain_palier = _palier_suivant(campagne, len(lot), maintenant)
        campagne.save(update_fields=["prochain_palier", "modifie_le"])
    return len(lot)


def _remettre_en_file(campagne: Campagne, taille: int) -> int:
    """Après une reprise : remet en file un lot de messages retenus pendant la
    pause. Chaque remise porte sa propre clé dans la boîte d'envoi."""
    repris = changer_etats_campagne(
        campagne.id,
        depuis=(Etat.EN_ATTENTE,),
        vers=Etat.EN_FILE,
        depuis_le=campagne.confirmee_le,
        source=Evenement.Source.WORKER,
        limite=taille,
    )
    prioritaire = campagne.type != Campagne.Type.MARKETING
    reprise = timezone.now().strftime("%Y%m%dT%H%M%S%f")
    for message in repris:
        mettre_en_file(message, prioritaire=prioritaire, reprise=reprise)
    return len(repris)


def hors_fenetre(campagne: Campagne, instant: datetime) -> bool:
    """Vrai si la campagne est marketing et que la fenêtre légale de son pays
    est fermée à cet instant. Une campagne transactionnelle n'attend jamais."""
    return campagne.type == Campagne.Type.MARKETING and not fenetre.ouverte(
        instant, campagne.client.pays
    )


def retenue(message) -> str | None:
    """Retenue enregistrée auprès de la soumission : un message d'une campagne
    en pause attend, celui d'une campagne annulée ne partira pas, et celui
    d'une campagne marketing attend, hors de la fenêtre légale, l'ouverture
    suivante, où l'ordonnanceur le remet en file (T-72)."""
    if not message.campagne_id:
        return None
    lue = (
        Campagne.objects.filter(id=message.campagne_id)
        .values_list("etat", "type", "client__pays")
        .first()
    )
    if lue is None:
        return None
    etat, nature, pays = lue
    if etat == Campagne.Etat.EN_PAUSE:
        return controles.RETENIR
    if etat == Campagne.Etat.ANNULEE:
        return controles.ANNULER
    if nature == Campagne.Type.MARKETING and not fenetre.ouverte(timezone.now(), pays):
        return controles.RETENIR
    return None


# Messages soumis d'une campagne dont la consommation n'est pas encore écrite :
# un worker interrompu entre la soumission et le règlement.
NON_REGLES = """
SELECT m.id, m.cout_total FROM journal_message AS m
WHERE m.campagne_id = %s AND m.date_reception >= %s AND m.date_soumission IS NOT NULL
  AND NOT EXISTS (
    SELECT 1 FROM credits_registre AS e
    WHERE e.type = 'CONSOMMATION' AND e.reference = 'message:' || m.id::text
  )
"""


def _regler_retardataires(campagne: Campagne) -> None:
    """Règle les messages soumis dont le worker n'a pas encore écrit la
    consommation, avant que le reliquat soit libéré. Idempotent : si le worker
    y arrive ensuite, il trouve la consommation déjà écrite."""
    with connection.cursor() as curseur:
        curseur.execute(NON_REGLES, [campagne.id, campagne.confirmee_le])
        lignes = curseur.fetchall()
    for message_id, cout in lignes:
        credits.solder_message(
            client_id=campagne.client_id,
            message_id=message_id,
            campagne_id=campagne.id,
            montant=cout,
            soumis=True,
        )


def clore(campagne: Campagne) -> bool:
    """Clôt une campagne verrouillée par l'appelant, si plus rien n'est à
    publier ni à soumettre : règle les retardataires, inscrit le coût réel,
    libère le reliquat. Une campagne en cours devient TERMINEE ; une campagne
    annulée le reste. Renvoie vrai si la campagne a été close."""
    if campagne.terminee_le:
        return False
    if campagne.etat == Campagne.Etat.EN_COURS:
        a_publier = Destinataire.objects.filter(campagne=campagne).exclude(
            Exists(Servi.objects.filter(campagne_id=campagne.id, contact_id=OuterRef("contact_id")))
        )
        if a_publier.exists():
            return False
    elif campagne.etat != Campagne.Etat.ANNULEE:
        return False
    if campagne.demarree_le and reste_a_soumettre(campagne.id, campagne.confirmee_le):
        return False
    _regler_retardataires(campagne)
    reservation = (
        Reservation.objects.filter(campagne_id=campagne.id)
        .values_list("id", "montant_initial", "montant_restant", "etat")
        .first()
    )
    libere = 0
    if reservation:
        reservation_id, initial, restant, etat = reservation
        campagne.cout_reel = initial - restant
        if etat == Reservation.Etat.ACTIVE:
            libere = credits.liberer(reservation_id)
    if campagne.etat == Campagne.Etat.EN_COURS:
        campagne.etat = Campagne.Etat.TERMINEE
    campagne.terminee_le = timezone.now()
    campagne.save(update_fields=["etat", "cout_reel", "terminee_le", "modifie_le"])
    auditer(
        "campagnes.cloture",
        campagne,
        client=campagne.client,
        apres={"etat": campagne.etat, "cout_reel": campagne.cout_reel, "libere": libere},
    )
    return True


def _verrouillee(campagne_id) -> Campagne:
    return (
        Campagne.objects.select_for_update(of=("self",))
        .select_related("client")
        .get(id=campagne_id)
    )


@transaction.atomic
def annuler_lot(campagne_id, taille: int = TAILLE_LOT) -> int:
    """Annule un lot de messages non soumis d'une campagne annulée. Renvoie le
    nombre de messages annulés : zéro quand il n'en reste plus, ou quand ceux
    qui restent sont en cours de soumission."""
    campagne = _verrouillee(campagne_id)
    if campagne.etat != Campagne.Etat.ANNULEE or not campagne.demarree_le:
        return 0
    if campagne.terminee_le:
        return 0
    return len(
        changer_etats_campagne(
            campagne.id,
            depuis=NON_SOUMIS,
            vers=Etat.ANNULE,
            depuis_le=campagne.confirmee_le,
            source=Evenement.Source.WORKER,
            motif=MOTIF_ANNULE,
            limite=taille,
        )
    )


@transaction.atomic
def cloturer(campagne_id) -> bool:
    """Clôt la campagne si plus rien n'est à publier ni à soumettre. Renvoie
    vrai si elle a été close."""
    return clore(_verrouillee(campagne_id))


def campagnes_a_publier() -> list:
    """Les campagnes que l'ordonnanceur doit faire avancer."""
    return list(
        Campagne.objects.filter(
            etat__in=(Campagne.Etat.EN_ATTENTE, Campagne.Etat.EN_COURS)
        ).values_list("id", flat=True)
    )


def campagnes_a_clore() -> list:
    return list(
        Campagne.objects.filter(
            etat__in=(Campagne.Etat.EN_COURS, Campagne.Etat.ANNULEE), terminee_le__isnull=True
        ).values_list("id", flat=True)
    )


def publier(lots_max: int = 20, maintenant: datetime | None = None) -> int:
    """Un tour d'ordonnanceur : quelques lots par campagne, pour que le relais
    des événements et les autres campagnes avancent aussi ; puis quelques pas
    de clôture pour les campagnes qui n'ont plus rien à publier."""
    publies = 0
    for campagne_id in campagnes_a_publier():
        try:
            for _ in range(lots_max):
                nombre = publier_lot(campagne_id, maintenant=maintenant)
                publies += nombre
                if nombre == 0:
                    break
        except Exception:
            # Une campagne en échec n'arrête ni les autres ni l'ordonnanceur.
            journal.exception("Publication de la campagne %s en échec.", campagne_id)
    for campagne_id in campagnes_a_clore():
        try:
            # Quelques lots d'annulation tant qu'il y en a, puis une seule
            # tentative de clôture : une campagne qui attend un message en cours
            # de soumission ne fait pas tourner l'ordonnanceur à vide.
            for _ in range(lots_max):
                if annuler_lot(campagne_id) == 0:
                    break
            cloturer(campagne_id)
        except Exception:
            journal.exception("Clôture de la campagne %s en échec.", campagne_id)
    return publies
