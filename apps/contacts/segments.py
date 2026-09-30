"""Segments : contacts choisis par critères (T-50).

Critères pris en charge : opérateur, ville, date d'entrée, liste, et champs
personnalisés déclarés. Le nombre annoncé est celui des contacts actifs hors
liste noire, c'est-à-dire ce que la diffusion enverra.
"""

from datetime import date, datetime, time

from django.db.models import Q
from django.utils import timezone

from . import liste_noire
from .champs import noms_declares
from .models import Contact, ListeContact, Segment

OPERATIONS = {
    "operateur": {"egal", "parmi"},
    "ville": {"egal", "parmi"},
    "date_entree": {"avant", "apres"},
    "liste": {"egal"},
}


class SegmentInvalide(ValueError):
    pass


def _valeurs(operation: str, valeur) -> list[str]:
    if operation == "parmi":
        if not isinstance(valeur, list) or not valeur:
            raise SegmentInvalide("« Parmi » attend une liste de valeurs.")
        return [str(v) for v in valeur]
    return [str(valeur)]


def _minuit(valeur) -> datetime:
    try:
        jour = date.fromisoformat(str(valeur))
    except ValueError:
        raise SegmentInvalide(f"Date invalide : {valeur}.") from None
    # Comparer la colonne elle-même à un instant, pour que l'index serve.
    return timezone.make_aware(datetime.combine(jour, time.min))


def _condition(critere: dict, client_id, champs: set[str]) -> Q:
    champ, operation, valeur = critere.get("champ"), critere.get("operation"), critere.get("valeur")
    if champ in champs:
        if operation != "egal":
            raise SegmentInvalide(f"Opération non prise en charge sur « {champ} ».")
        # Les valeurs importées sont des textes.
        return Q(champs__contains={champ: str(valeur)})
    if champ not in OPERATIONS or operation not in OPERATIONS[champ]:
        raise SegmentInvalide(f"Critère inconnu : {champ} {operation}.")
    if champ == "operateur":
        valeurs = _valeurs(operation, valeur)
        return Q(operateur_confirme__in=valeurs) | Q(
            operateur_confirme="", operateur_presume__in=valeurs
        )
    if champ == "ville":
        return Q(ville__in=_valeurs(operation, valeur))
    if champ == "date_entree":
        instant = _minuit(valeur)
        return Q(cree_le__lt=instant) if operation == "avant" else Q(cree_le__gte=instant)
    membres = ListeContact.objects.filter(liste_id=valeur, client_id=client_id)
    return Q(id__in=membres.values("contact_id"))


def filtrer(client, criteres: list[dict]):
    """Les contacts du client qui répondent aux critères, actifs ou non, liste
    noire comprise. Le filtre par client est explicite : la confirmation d'une
    campagne passe par un rôle qui ignore le cloisonnement."""
    champs = noms_declares(client)
    condition = Q()
    for critere in criteres:
        condition &= _condition(critere, client.id, champs)
    return Contact.objects.filter(condition, client=client)


def contacts(client, criteres: list[dict]):
    """Les contacts visés, actifs et hors liste noire : ceux que la diffusion servira."""
    return liste_noire.destinataires(client.id, filtrer(client, criteres))


def creer(*, client, nom: str, criteres: list[dict]) -> tuple[Segment, int]:
    """Crée le segment et renvoie son estimation. Un segment vide est signalé
    dès sa création, pas au lancement d'une campagne."""
    if not criteres:
        raise SegmentInvalide("Un segment porte au moins un critère.")
    nombre = contacts(client, criteres).count()
    if nombre == 0:
        raise SegmentInvalide("Aucun contact ne répond à ces critères : le segment serait vide.")
    segment = Segment.objects.create(
        client=client,
        nom=nom.strip(),
        criteres=criteres,
        nombre_estime=nombre,
        calcule_le=timezone.now(),
    )
    return segment, nombre


def recalculer(segment: Segment) -> int:
    """Recalcul à l'aperçu d'une campagne. Un segment désactivé ne se recalcule pas."""
    if not segment.actif:
        raise SegmentInvalide(segment.motif_desactivation or "Segment désactivé.")
    nombre = contacts(segment.client, segment.criteres).count()
    Segment.objects.filter(id=segment.id).update(nombre_estime=nombre, calcule_le=timezone.now())
    return nombre


def figer(segment: Segment) -> list:
    """Les contacts du segment à l'instant de la confirmation d'une campagne :
    ceux-là, et eux seuls, seront servis et facturés (décision F-15)."""
    if not segment.actif:
        raise SegmentInvalide(segment.motif_desactivation or "Segment désactivé.")
    return list(contacts(segment.client, segment.criteres).values_list("id", flat=True))
