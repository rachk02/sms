"""Liste noire : par client, et de la plateforme (décision F-16).

Le filtrage s'applique deux fois : à la constitution d'une campagne, pour que
le nombre de destinataires et le coût annoncés en tiennent compte, puis juste
avant la soumission de chaque message, car un STOP peut arriver pendant une
diffusion qui dure des heures. Le second contrôle est enregistré auprès de
`apps.core.controles` : tout chemin d'envoi le passe.
"""

from django.db import IntegrityError, transaction
from django.db.models import Q

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
    """Contrôle avant soumission, enregistré au démarrage."""
    if bloque(message.client_id, message.numero_normalise):
        return MOTIF_LISTE_NOIRE
    return None


def destinataires(client_id, contacts):
    """Les contacts actifs d'un ensemble, hors liste noire : ce qu'une campagne
    enverra, donc ce qui s'annonce et se facture."""
    return contacts.filter(client_id=client_id, actif=True).exclude(
        numero_normalise__in=numeros_bloques(client_id)
    )
