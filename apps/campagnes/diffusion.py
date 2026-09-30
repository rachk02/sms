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
"""

from django.db import transaction
from django.db.models import Exists, OuterRef
from django.utils import timezone

from apps.contacts.models import Contact
from apps.credits.tarifs import prix_segment
from apps.expediteurs.controle import Action, choisir
from apps.journal.models import Destinataire as Servi
from apps.journal.models import Evenement
from apps.journal.services import enregistrer_message, mettre_en_file
from apps.telecom.numero_court import numero_court

from .models import Campagne, Destinataire
from .personnalisation import compiler

TAILLE_LOT = 500
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


@transaction.atomic
def publier_lot(campagne_id, taille: int = TAILLE_LOT) -> int:
    """Publie le lot suivant d'une campagne. Renvoie le nombre de messages
    publiés, zéro quand il ne reste rien à publier pour l'instant."""
    campagne = (
        Campagne.objects.select_for_update(of=("self",))
        .select_related("client", "expediteur")
        .get(id=campagne_id)
    )
    if campagne.etat not in (Campagne.Etat.EN_ATTENTE, Campagne.Etat.EN_COURS):
        return 0
    if campagne.etat == Campagne.Etat.EN_ATTENTE:
        campagne.etat, campagne.demarree_le = Campagne.Etat.EN_COURS, timezone.now()
        campagne.save(update_fields=["etat", "demarree_le", "modifie_le"])
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
    return len(lot)


def campagnes_a_publier() -> list:
    """Les campagnes que l'ordonnanceur doit faire avancer."""
    return list(
        Campagne.objects.filter(
            etat__in=(Campagne.Etat.EN_ATTENTE, Campagne.Etat.EN_COURS)
        ).values_list("id", flat=True)
    )


def publier(lots_max: int = 20) -> int:
    """Un tour d'ordonnanceur : quelques lots par campagne, pour que le relais
    des événements et les autres campagnes avancent aussi."""
    publies = 0
    for campagne_id in campagnes_a_publier():
        for _ in range(lots_max):
            nombre = publier_lot(campagne_id)
            publies += nombre
            if nombre == 0:
                break
    return publies
