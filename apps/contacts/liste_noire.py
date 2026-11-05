"""Liste noire : par client, et de la plateforme (décision F-16).

Le filtrage s'applique deux fois : à la constitution d'une campagne, pour que
le nombre de destinataires et le coût annoncés en tiennent compte, puis juste
avant la soumission de chaque message, car un STOP peut arriver pendant une
diffusion qui dure des heures. Le second contrôle est enregistré auprès de
`apps.core.controles` : tout chemin d'envoi le passe.
"""

from django.db import IntegrityError, transaction
from django.db.models import Q

from apps.comptes.models import Client
from apps.core.evenements import publier
from apps.core.numeros import normaliser

from .models import ListeNoire

MOTIF_LISTE_NOIRE = "LISTE_NOIRE"


def bloquer(*, client, numero: str, origine: str, message_origine: str = "", motif: str = ""):
    """Inscrit un numéro sur la liste noire du client. Sans effet s'il y est
    déjà : une même désinscription reçue deux fois reste une seule ligne."""
    numero = normaliser(numero, client.pays)
    try:
        with transaction.atomic():
            return ListeNoire.objects.create(
                client=client,
                numero_normalise=numero,
                origine=origine,
                message_origine=message_origine,
                motif=motif,
            )
    except IntegrityError:
        return ListeNoire.objects.get(client=client, numero_normalise=numero)


# Annonce d'une désinscription inscrite : le journal envoie la confirmation, et
# le webhook du client la recevra (lot 9, T-111).
DESINSCRIPTION_INSCRITE = "contacts.desinscription.inscrite"


@transaction.atomic
def desinscrire(
    *,
    client_id,
    entrant_id,
    numero_normalise: str,
    texte: str,
    operateur: str = "",
    confirmation: str = "",
):
    """Inscrit sur la liste noire du client un STOP rapproché de lui (T-70),
    avec le message reçu mot pour mot, puis annonce l'inscription (T-71). Un
    numéro déjà sur sa liste y reste avec sa première preuve ; le STOP reçu
    demeure dans les messages entrants, et la confirmation part quand même."""
    entree = bloquer(
        client=Client.objects.get(id=client_id),
        numero=numero_normalise,
        origine=ListeNoire.Origine.STOP_SMS,
        message_origine=texte,
    )
    publier(
        DESINSCRIPTION_INSCRITE,
        client_id=client_id,
        objet_id=entree.id,
        donnees={
            "numero_normalise": entree.numero_normalise,
            "message_origine": texte,
            "entrant_id": str(entrant_id),
            "inscrit_le": entree.ajoute_le.isoformat(),
            "operateur": operateur,
            "confirmation": confirmation,
        },
        cle_idempotence=f"inscrite:{entrant_id}:{client_id}",
    )
    return entree


def bloquer_partout(*, numero: str, origine: str, motif: str, agent, adresse_ip=None):
    """Inscrit un numéro sur la liste noire de la plateforme. Réservé au
    back-office ; tracé au journal d'audit."""
    from apps.core.audit import auditer

    numero = normaliser(numero)
    with transaction.atomic():
        entree, cree = ListeNoire.objects.get_or_create(
            client=None,
            numero_normalise=numero,
            defaults={"origine": origine, "motif": motif},
        )
        if cree:
            auditer(
                "contacts.liste_noire.plateforme",
                entree,
                auteur=agent,
                apres={"numero": numero, "origine": origine},
                motif=motif,
                adresse_ip=adresse_ip,
            )
    return entree


def bloque(client_id, numero_normalise: str) -> bool:
    return ListeNoire.objects.filter(
        Q(client_id=client_id) | Q(client__isnull=True), numero_normalise=numero_normalise
    ).exists()


def numeros_bloques(client_id):
    """Sous-requête des numéros exclus pour un client : les siens et ceux de la
    plateforme. À combiner avec les contacts visés pour compter les destinataires."""
    return ListeNoire.objects.filter(Q(client_id=client_id) | Q(client__isnull=True)).values(
        "numero_normalise"
    )


def controle_liste_noire(message) -> str | None:
    """Contrôle avant soumission, enregistré au démarrage. La confirmation d'une
    désinscription, message système, part vers un numéro qui vient justement
    d'entrer sur la liste : c'est la seule exemption."""
    if message.nature == "SYSTEME":
        return None
    if bloque(message.client_id, message.numero_normalise):
        return MOTIF_LISTE_NOIRE
    return None


def destinataires(client_id, contacts):
    """Les contacts actifs d'un ensemble, hors liste noire : ce qu'une campagne
    enverra, donc ce qui s'annonce et se facture."""
    return contacts.filter(client_id=client_id, actif=True).exclude(
        numero_normalise__in=numeros_bloques(client_id)
    )
