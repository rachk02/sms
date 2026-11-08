"""Billetterie de support (T-102) : la seule porte d'écriture des tickets.

Le client ouvre un ticket depuis l'application, au besoin depuis une fiche
message ou une campagne : le contexte technique est relu ici, côté serveur, à
partir de l'objet désigné, jamais pris dans la saisie. Un agent ressaisit en
ticket toute demande arrivée par un autre canal. Les états suivent la table
TRANSITIONS ; chaque changement tient le compteur d'engagement. L'ordonnanceur
envoie le préavis et clôt les tickets laissés sans réponse
(`traiter_echeances`).
"""

from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.core.audit import auditer
from apps.core.courriels import envoyer
from apps.core.evenements import publier

from . import engagement
from .models import Incident, MessageTicket, PieceTicket, PublicationIncident, Ticket

E = Ticket.Etat

TRANSITIONS = {
    E.NOUVEAU: {E.PRIS_EN_CHARGE, E.ATTENTE_CLIENT, E.ESCALADE_N2, E.ESCALADE_RESEAU, E.RESOLU},
    E.PRIS_EN_CHARGE: {E.ATTENTE_CLIENT, E.ESCALADE_N2, E.ESCALADE_RESEAU, E.RESOLU},
    E.ATTENTE_CLIENT: {E.PRIS_EN_CHARGE, E.ESCALADE_N2, E.ESCALADE_RESEAU, E.RESOLU, E.CLOS},
    E.ESCALADE_N2: {E.PRIS_EN_CHARGE, E.ATTENTE_CLIENT, E.ESCALADE_RESEAU, E.RESOLU},
    E.ESCALADE_RESEAU: {E.PRIS_EN_CHARGE, E.ATTENTE_CLIENT, E.ESCALADE_N2, E.RESOLU},
    E.RESOLU: {E.PRIS_EN_CHARGE, E.CLOS},
    E.CLOS: {E.PRIS_EN_CHARGE},
}

PREAVIS = timedelta(days=5)
CLOTURE_SANS_REPONSE = timedelta(days=7)
CLOTURE_APRES_RESOLUTION = timedelta(hours=48)
# Un ticket clos depuis moins longtemps se rouvre ; au-delà, une réponse ouvre
# un nouveau ticket qui cite le précédent.
REOUVERTURE = timedelta(days=7)


class TicketRefuse(ValueError):
    pass


# Contexte technique


def contexte_message(client_id, message_id, recu) -> dict | None:
    """Le contexte d'une fiche message, lu borné sur sa date de réception et
    sur le compte : le message d'un autre compte est introuvable."""
    from apps.journal.selectors import fiche

    trouvee = fiche(client_id, message_id, recu)
    if trouvee is None:
        return None
    return {"objet": "message", **trouvee.contexte_ticket()}


def contexte_campagne(client_id, campagne_id) -> dict | None:
    from apps.campagnes import selectors as suivis
    from apps.campagnes.models import Campagne

    campagne = (
        Campagne.objects.filter(client_id=client_id, id=campagne_id)
        .select_related("expediteur")
        .first()
    )
    if campagne is None:
        return None

    def instant(valeur):
        return valeur.isoformat() if valeur else ""

    contexte = {
        "objet": "campagne",
        "campagne_id": str(campagne.id),
        "nom": campagne.nom,
        "type": campagne.type,
        "etat": campagne.etat,
        "expediteur": campagne.expediteur.libelle if campagne.expediteur else "",
        "destinataires_prevus": campagne.destinataires_prevus,
        "cout_estime": campagne.cout_estime,
        "cout_reel": campagne.cout_reel,
        "planifiee_pour": instant(campagne.planifiee_pour),
        "confirmee_le": instant(campagne.confirmee_le),
        "demarree_le": instant(campagne.demarree_le),
        "terminee_le": instant(campagne.terminee_le),
    }
    suivi = suivis.suivi(campagne)
    if suivi:
        contexte |= {
            "partis": suivi.partis,
            "livres": suivi.livres,
            "echecs": suivi.echecs,
            "sans_accuse": suivi.sans_accuse,
            "refuses": suivi.refuses,
            "non_soumis": suivi.non_soumis,
        }
    return contexte


# Ouverture


def _adresse_ticket(ticket: Ticket) -> str:
    return f"{settings.PORTAIL_URL.rstrip('/')}/support/{ticket.id}"


def _delai_annonce(ticket: Ticket) -> str:
    regle = engagement.engagement(ticket)
    minutes = regle.delai_min
    if minutes < 60:
        duree = f"{minutes} minutes"
    elif minutes % (engagement.HEURES_PAR_JOUR * 60) == 0 and not regle.continu:
        jours = minutes // (engagement.HEURES_PAR_JOUR * 60)
        duree = f"{jours} jour{'s' if jours > 1 else ''} ouvré{'s' if jours > 1 else ''}"
    else:
        heures = minutes // 60
        duree = f"{heures} heure{'s' if heures > 1 else ''}"
        duree += "" if regle.continu else " ouvrées" if heures > 1 else " ouvrée"
    return duree


def _courriel(ticket: Ticket, sujet: str, texte: str) -> None:
    """Au demandeur s'il est encore membre actif du compte, relu à l'envoi ;
    sinon aux administrateurs actifs : un membre retiré ne reçoit plus le fil."""
    from apps.comptes.selectors import courriels_a_prevenir

    if not ticket.demandeur_id:
        return
    for destinataire in courriels_a_prevenir(ticket.client_id, ticket.demandeur_id):
        envoyer(
            destinataire,
            f"[Ticket {ticket.reference}] {sujet}",
            f"{texte}\n\nCompte : {ticket.client.raison_sociale}\n"
            f"Ticket : {ticket.objet}\n{_adresse_ticket(ticket)}\n",
        )


@transaction.atomic
def ouvrir(
    *,
    client,
    demandeur,
    categorie: str,
    objet: str,
    corps: str,
    contexte: dict | None = None,
    canal: str = Ticket.Canal.FORMULAIRE,
    agent=None,
    precedent: Ticket | None = None,
    pieces=(),
    maintenant=None,
) -> Ticket:
    """Ouvre un ticket et son premier message. `agent` est l'agent qui
    ressaisit une demande arrivée par un autre canal ; il en est l'auteur."""
    maintenant = maintenant or timezone.now()
    objet, corps = (objet or "").strip(), (corps or "").strip()
    if not objet or not corps:
        raise TicketRefuse("Indiquez l'objet et le détail de votre demande.")
    if categorie not in Ticket.Categorie.values:
        raise TicketRefuse("Choisissez une catégorie.")
    if demandeur is not None and demandeur.client_id != client.id:
        raise TicketRefuse("Le demandeur n'est pas membre de ce compte.")
    ticket = Ticket(
        client=client,
        demandeur=demandeur,
        canal=canal,
        categorie=categorie,
        objet=objet[:200],
        contexte=contexte or {},
        precedent=precedent,
        engagement_depuis=maintenant,
    )
    ticket.priorite = engagement.engagement(ticket).priorite
    ticket.save()
    premier = MessageTicket.objects.create(
        client=client, ticket=ticket, auteur=agent or demandeur, corps=corps
    )
    _joindre(premier, pieces)
    if agent is not None:
        auditer(
            "support.ticket.saisie",
            ticket,
            auteur=agent,
            client=client,
            apres={"canal": canal, "categorie": categorie, "objet": ticket.objet},
        )
    publier("support.ticket.ouvert", client_id=client.id, objet_id=ticket.id)
    _courriel(
        ticket,
        "Demande reçue",
        f"Votre demande est enregistrée sous la référence {ticket.reference}.\n"
        f"Catégorie retenue : {ticket.get_categorie_display()} ({ticket.priorite}).\n"
        f"Première réponse sous {_delai_annonce(ticket)}."
        + (f"\nElle fait suite au ticket {precedent.reference}." if precedent else ""),
    )
    return ticket


# Changements d'état


def _changer(ticket: Ticket, vers: str, maintenant) -> str:
    """Applique un changement d'état et tient le compteur ; rend l'état
    quitté. À appeler sous verrou du ticket."""
    depuis = ticket.etat
    if vers == depuis:
        return depuis
    if vers not in TRANSITIONS[depuis]:
        raise TicketRefuse(
            f"Un ticket {Ticket.Etat(depuis).label.lower()} ne passe pas à "
            f"{Ticket.Etat(vers).label.lower()}."
        )
    feries = engagement.feries_entre(
        (ticket.engagement_depuis or maintenant).date(), maintenant.date()
    )
    if vers in engagement.EN_COURS:
        engagement.relancer(ticket, maintenant)
    else:
        engagement.arreter(ticket, maintenant, feries)
    if depuis == E.ATTENTE_CLIENT:
        ticket.attente_depuis = ticket.preavis_le = None
    if vers == E.ATTENTE_CLIENT:
        ticket.attente_depuis, ticket.preavis_le = maintenant, None
    if depuis == E.ESCALADE_RESEAU:
        ticket.relance_le = None
    if vers == E.RESOLU:
        ticket.resolu_le = maintenant
    if vers == E.CLOS:
        ticket.clos_le = maintenant
    if vers == E.PRIS_EN_CHARGE and depuis in (E.RESOLU, E.CLOS):
        ticket.resolu_le = ticket.clos_le = None
    ticket.etat = vers
    return depuis


def _verrouiller(ticket: Ticket) -> Ticket:
    return (
        Ticket.objects.select_for_update(of=("self",))
        .select_related("client", "demandeur")
        .get(id=ticket.id)
    )


@transaction.atomic
def changer_etat(
    ticket: Ticket,
    vers: str,
    *,
    agent,
    reference_reseau: str = "",
    relance_le=None,
    resolution: str = "",
    adresse_ip=None,
    maintenant=None,
) -> Ticket:
    """Changement d'état par un agent. L'escalade à l'opérateur exige la
    référence de son dossier et la date de relance prévue ; la résolution,
    ce qui a été retenu."""
    maintenant = maintenant or timezone.now()
    ticket = _verrouiller(ticket)
    reference_reseau = (reference_reseau or "").strip()
    resolution = (resolution or "").strip()
    if vers == E.ESCALADE_RESEAU and (not reference_reseau or relance_le is None):
        raise TicketRefuse(
            "Une escalade à l'opérateur exige la référence de son dossier et la date de relance."
        )
    if vers == E.RESOLU and not resolution:
        raise TicketRefuse("Indiquez la résolution retenue : elle est envoyée au client.")
    avant = {"etat": ticket.etat}
    depuis = _changer(ticket, vers, maintenant)
    if vers == E.ESCALADE_RESEAU:
        ticket.reference_reseau, ticket.relance_le = reference_reseau, relance_le
    if vers == E.RESOLU:
        ticket.resolution = resolution
    if ticket.agent_id is None:
        ticket.agent = agent
    ticket.save()
    auditer(
        "support.ticket.etat",
        ticket,
        auteur=agent,
        client=ticket.client,
        avant=avant,
        apres={
            "etat": vers,
            **({"reference_reseau": reference_reseau} if vers == E.ESCALADE_RESEAU else {}),
        },
        adresse_ip=adresse_ip,
    )
    if vers == E.RESOLU and depuis != E.RESOLU:
        _courriel(
            ticket,
            "Demande résolue",
            f"Résolution retenue :\n\n{resolution}\n\nSans réaction de votre part, le ticket "
            "sera clos dans quarante-huit heures. Une réponse le rouvre.",
        )
    return ticket


@transaction.atomic
def prendre_en_charge(ticket: Ticket, *, agent, maintenant=None) -> Ticket:
    maintenant = maintenant or timezone.now()
    ticket = _verrouiller(ticket)
    ticket.agent = agent
    if ticket.etat == E.NOUVEAU:
        _changer(ticket, E.PRIS_EN_CHARGE, maintenant)
    ticket.save()
    return ticket


@transaction.atomic
def recategoriser(ticket: Ticket, categorie: str, *, agent, adresse_ip=None) -> Ticket:
    """La catégorie, corrigée par l'agent, refixe la priorité et le délai."""
    if categorie not in Ticket.Categorie.values:
        raise TicketRefuse("Catégorie inconnue.")
    ticket = _verrouiller(ticket)
    avant = {"categorie": ticket.categorie, "priorite": ticket.priorite}
    ticket.categorie = categorie
    ticket.priorite = engagement.engagement(ticket).priorite
    ticket.save()
    auditer(
        "support.ticket.categorie",
        ticket,
        auteur=agent,
        client=ticket.client,
        avant=avant,
        apres={"categorie": ticket.categorie, "priorite": ticket.priorite},
        adresse_ip=adresse_ip,
    )
    return ticket


# Réponses


def _fil_public(ticket: Ticket) -> str:
    lignes = []
    for message in ticket.messages.filter(interne=False).select_related("auteur"):
        auteur = "EL-IMMEKA" if message.auteur and message.auteur.is_staff else "Vous"
        moment = timezone.localtime(message.cree_le)
        lignes.append(f"{auteur}, le {moment:%d/%m/%Y à %H:%M} :\n{message.corps}")
    return "\n\n".join(reversed(lignes))


def _joindre(message: MessageTicket, pieces) -> None:
    from apps.fichiers.models import Fichier
    from apps.fichiers.services import deposer

    for piece in pieces or ():
        fichier = deposer(
            client=message.client,
            famille=Fichier.Famille.PIECE,
            flux=piece,
            nom_origine=getattr(piece, "name", "piece"),
        )
        PieceTicket.objects.create(client=message.client, message=message, fichier=fichier)


def repondre_client(ticket: Ticket, *, auteur, corps: str, pieces=(), maintenant=None) -> Ticket:
    """Réponse d'un membre du compte. Elle rouvre un ticket résolu ou clos
    depuis moins de sept jours ; au-delà, elle ouvre un nouveau ticket qui
    cite le précédent. Rend le ticket qui a reçu la réponse."""
    maintenant = maintenant or timezone.now()
    corps = (corps or "").strip()
    if not corps:
        raise TicketRefuse("Votre réponse est vide.")
    with transaction.atomic():
        ticket = _verrouiller(ticket)
        if ticket.etat == E.CLOS and ticket.clos_le and maintenant - ticket.clos_le >= REOUVERTURE:
            suite = None
        else:
            suite = ticket
            message = MessageTicket.objects.create(
                client=ticket.client, ticket=ticket, auteur=auteur, corps=corps
            )
            _joindre(message, pieces)
            if ticket.etat in (E.ATTENTE_CLIENT, E.RESOLU, E.CLOS):
                _changer(ticket, E.PRIS_EN_CHARGE, maintenant)
            ticket.save()
            publier("support.ticket.reponse_client", client_id=ticket.client_id, objet_id=ticket.id)
    if suite is not None:
        return suite
    return ouvrir(
        client=ticket.client,
        demandeur=auteur,
        categorie=ticket.categorie,
        objet=f"Suite du ticket {ticket.reference} : {ticket.objet}"[:200],
        corps=corps,
        contexte=ticket.contexte,
        precedent=ticket,
        pieces=pieces,
        maintenant=maintenant,
    )


@transaction.atomic
def repondre_agent(
    ticket: Ticket,
    *,
    agent,
    corps: str,
    interne: bool = False,
    attendre_client: bool = False,
    maintenant=None,
) -> Ticket:
    """Réponse d'un agent, ou note interne. La première réponse publique
    tient l'engagement ; `attendre_client` met le ticket en attente
    d'information, ce qui arrête le compteur."""
    maintenant = maintenant or timezone.now()
    corps = (corps or "").strip()
    if not corps:
        raise TicketRefuse("Le message est vide.")
    if interne and attendre_client:
        raise TicketRefuse("Une note interne n'attend rien du client.")
    ticket = _verrouiller(ticket)
    if ticket.etat == E.CLOS:
        raise TicketRefuse("Ce ticket est clos : seul le client le rouvre en répondant.")
    MessageTicket.objects.create(
        client=ticket.client, ticket=ticket, auteur=agent, corps=corps, interne=interne
    )
    if ticket.agent_id is None:
        ticket.agent = agent
    if not interne:
        if ticket.premiere_reponse_le is None:
            ticket.premiere_reponse_le = maintenant
        if attendre_client:
            _changer(ticket, E.ATTENTE_CLIENT, maintenant)
        elif ticket.etat == E.NOUVEAU:
            _changer(ticket, E.PRIS_EN_CHARGE, maintenant)
    ticket.save()
    if not interne:
        publier("support.ticket.repondu", client_id=ticket.client_id, objet_id=ticket.id)
        _courriel(
            ticket,
            "Réponse de l'équipe EL-IMMEKA",
            ("Nous attendons votre réponse pour avancer.\n\n" if attendre_client else "")
            + _fil_public(ticket),
        )
    return ticket


# Échéances, par l'ordonnanceur


def traiter_echeances(maintenant=None) -> dict:
    """Préavis au cinquième jour d'attente, clôture au septième ; clôture des
    tickets résolus depuis quarante-huit heures. Rejouable : chaque ticket est
    relu sous verrou et ne change qu'une fois."""
    maintenant = maintenant or timezone.now()
    faits = {"preavis": 0, "clos_sans_reponse": 0, "clos_resolus": 0}
    a_prevenir = Ticket.objects.filter(
        etat=E.ATTENTE_CLIENT,
        attente_depuis__lte=maintenant - PREAVIS,
        attente_depuis__gt=maintenant - CLOTURE_SANS_REPONSE,
        preavis_le__isnull=True,
    ).values_list("id", flat=True)
    for ident in list(a_prevenir):
        with transaction.atomic():
            ticket = _verrouiller(Ticket(id=ident))
            if ticket.etat != E.ATTENTE_CLIENT or ticket.preavis_le:
                continue
            ticket.preavis_le = maintenant
            ticket.save(update_fields=["preavis_le", "modifie_le"])
            _courriel(
                ticket,
                "Votre réponse est attendue",
                "Nous attendons votre réponse depuis cinq jours. Sans réponse dans les deux "
                "jours, le ticket sera clos ; vous pourrez le rouvrir en répondant.",
            )
            faits["preavis"] += 1
    a_clore = [
        (ident, E.ATTENTE_CLIENT, "clos_sans_reponse")
        for ident in Ticket.objects.filter(
            etat=E.ATTENTE_CLIENT, attente_depuis__lte=maintenant - CLOTURE_SANS_REPONSE
        ).values_list("id", flat=True)
    ] + [
        (ident, E.RESOLU, "clos_resolus")
        for ident in Ticket.objects.filter(
            etat=E.RESOLU, resolu_le__lte=maintenant - CLOTURE_APRES_RESOLUTION
        ).values_list("id", flat=True)
    ]
    for ident, attendu, compte in a_clore:
        with transaction.atomic():
            ticket = _verrouiller(Ticket(id=ident))
            if ticket.etat != attendu:
                continue
            _changer(ticket, E.CLOS, maintenant)
            ticket.save()
            sans_reponse = attendu == E.ATTENTE_CLIENT
            _courriel(
                ticket,
                "Ticket clos",
                (
                    "Sans réponse de votre part depuis sept jours, le ticket est clos."
                    if sans_reponse
                    else f"Résolution retenue :\n\n{ticket.resolution}"
                )
                + "\n\nPour le rouvrir dans les sept jours, répondez depuis l'application ; "
                "au-delà, votre réponse ouvrira un nouveau ticket.",
            )
            faits[compte] += 1
    return faits


# Incidents de la page d'état (T-103)


class IncidentRefuse(ValueError):
    pass


@transaction.atomic
def declarer_incident(
    *, titre: str, composants: list, etat: str, texte: str, agent, debut_le=None, adresse_ip=None
) -> Incident:
    """Déclare un incident, avec sa première publication : la constatation, ce
    qui est touché et ce qui ne l'est pas. Seul un humain déclare une panne.
    Une maintenance se déclare à l'avance, avec son début prévu."""
    maintenant = timezone.now()
    titre, texte = (titre or "").strip(), (texte or "").strip()
    if not titre or not texte or not composants:
        raise IncidentRefuse("Indiquez le titre, les composants touchés et la constatation.")
    if etat == Incident.Etat.RETABLI:
        raise IncidentRefuse("Un incident se déclare dégradé, en panne ou en maintenance.")
    debut_le = debut_le or maintenant
    if debut_le > maintenant and etat != Incident.Etat.MAINTENANCE:
        raise IncidentRefuse("Seule une maintenance se déclare à l'avance.")
    incident = Incident.objects.create(
        titre=titre[:200], composants=list(composants), etat=etat, debut_le=debut_le
    )
    PublicationIncident.objects.create(incident=incident, texte=texte, etat=etat, auteur=agent)
    auditer(
        "support.incident.declaration",
        incident,
        auteur=agent,
        apres={"titre": incident.titre, "composants": incident.composants, "etat": etat},
        adresse_ip=adresse_ip,
    )
    return incident


@transaction.atomic
def publier_incident(incident: Incident, *, texte: str, etat: str, agent, adresse_ip=None):
    """Une publication de plus : l'avancement, ou le rétablissement, qui clôt
    l'incident avec ce qui a été perdu et ce qui a été repris."""
    texte = (texte or "").strip()
    if not texte:
        raise IncidentRefuse("Le texte de la publication est vide.")
    incident = Incident.objects.select_for_update().get(id=incident.id)
    if incident.fin_le is not None:
        raise IncidentRefuse("Cet incident est clos.")
    avant = {"etat": incident.etat}
    incident.etat = etat
    if etat == Incident.Etat.RETABLI:
        incident.fin_le = timezone.now()
    incident.save()
    PublicationIncident.objects.create(incident=incident, texte=texte, etat=etat, auteur=agent)
    auditer(
        "support.incident.publication",
        incident,
        auteur=agent,
        avant=avant,
        apres={"etat": etat},
        adresse_ip=adresse_ip,
    )
    return incident
