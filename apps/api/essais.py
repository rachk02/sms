"""Bac à sable des intégrateurs (T-113).

Une clé `sk_test_` passe par les mêmes contrôles qu'une clé de production
(`campagnes.services.controler_unitaires` : numéro, réseau, texte, expéditeur,
tarif) et rend des réponses de même forme, mais rien ne part jamais : aucun
message n'est écrit au journal ni déposé dans une file d'envoi, aucun crédit
n'est réservé ni consommé, le plafond du compte n'est pas décompté. Les
messages d'essai vivent dans `api_message_essai`, cloisonnée, effacée après
trente jours (décision de l'utilisateur : une table séparée, que ni le journal,
ni les compteurs, ni le registre de crédits ne voient).

L'accusé simulé est choisi par le dernier chiffre du numéro (décision de
l'utilisateur) : 0 à 6 livré, 7 non livré, 8 expiré, 9 sans accusé.
L'ordonnanceur fait avancer chaque message (`avancer`) : soumis une seconde
après la réception, accusé quatre secondes après, constat sans accusé après
vingt secondes. L'issue finale part en webhook comme en production, avec
`environnement: TEST` dans les données.
"""

from dataclasses import dataclass, field
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from .models import MessageEssai

ISSUES = {
    **dict.fromkeys("0123456", "LIVRE"),
    "7": "NON_LIVRE",
    "8": "EXPIRE",
    "9": "SANS_ACCUSE",
}
MOTIFS = {"NON_LIVRE": "UNDELIV", "EXPIRE": "EXPIRED"}
DELAI_SOUMISSION = timedelta(seconds=1)
DELAI_ACCUSE = timedelta(seconds=4)
DELAI_SANS_ACCUSE = timedelta(seconds=20)
CONSERVATION = timedelta(days=30)
LOT_AVANCE = 500


def issue_de(numero_normalise: str) -> str:
    return ISSUES[numero_normalise[-1]]


def accepter(client, demandes: list[dict], *, cle_api: str):
    """Les mêmes contrôles qu'un envoi réel, puis une ligne d'essai par
    demande valide. Renvoie les sorts, de même forme qu'en production."""
    from apps.campagnes.services import controler_unitaires
    from apps.core.encodage import analyser

    sorts, valides = controler_unitaires(client, demandes)
    lignes = []
    for envoi, ligne, cout in valides:
        analyse = analyser(ligne["texte"])
        recu_le = ligne["date_reception"]
        lignes.append(
            MessageEssai(
                id=ligne["id"],
                client=client,
                cle_api=cle_api,
                numero_normalise=ligne["numero_normalise"],
                operateur=ligne["operateur"],
                expediteur_libelle=ligne["expediteur_libelle"],
                segments=analyse.segments,
                encodage=analyse.encodage,
                cout_unitaire=ligne["cout_unitaire"],
                cout_total=cout,
                issue=issue_de(ligne["numero_normalise"]),
                date_reception=recu_le,
                prochaine_etape_le=recu_le + DELAI_SOUMISSION,
                historique=[{"etat": "RECU", "horodatage": recu_le.isoformat()}],
                expire_le=recu_le + CONSERVATION,
            )
        )
        envoi.message = lignes[-1]
    MessageEssai.objects.bulk_create(lignes)
    return sorts


# Lecture, sous la même forme que le journal


@dataclass(frozen=True)
class Etape:
    etat: str
    horodatage: object


@dataclass(frozen=True)
class Fiche:
    message: MessageEssai
    etapes: list = field(default_factory=list)
    diagnostic: str = ""


def fiche(client_id, message_id) -> Fiche | None:
    from django.utils.dateparse import parse_datetime

    message = MessageEssai.objects.filter(client_id=client_id, id=message_id).first()
    if message is None:
        return None
    etapes = [Etape(e["etat"], parse_datetime(e["horodatage"])) for e in message.historique]
    return Fiche(message, etapes)


def rechercher(client_id, filtres, apres: str | None, taille: int):
    """Une page des messages d'essai, mêmes filtres, même tri et même curseur
    que le journal (`journal.selectors.rechercher`)."""
    from apps.journal.selectors import Page, RechercheRefusee, _curseur, _lire_curseur

    if filtres.au_dela_du_seuil:
        raise RechercheRefusee("Une recherche couvre 31 jours au plus.")
    if filtres.campagne_id:
        # Aucune campagne dans le bac à sable.
        return Page([], None)
    messages = MessageEssai.objects.filter(
        client_id=client_id,
        date_reception__gte=filtres.debut,
        date_reception__lt=filtres.fin,
    )
    if filtres.etat:
        messages = messages.filter(etat=filtres.etat)
    if filtres.numero:
        messages = messages.filter(numero_normalise=filtres.numero)
    if apres:
        moment, identifiant = _lire_curseur(apres)
        messages = messages.filter(date_reception__lte=moment).filter(
            Q(date_reception__lt=moment) | Q(date_reception=moment, id__lt=identifiant)
        )
    lot = list(messages.order_by("-date_reception", "-id")[: taille + 1])
    suivant = _curseur(lot[taille - 1]) if len(lot) > taille else None
    return Page(lot[:taille], suivant)


# Accusés simulés


def _annoncer(message: MessageEssai, horodatage) -> None:
    from apps.core.evenements import publier
    from apps.journal.services import WEBHOOK_SORTS

    nom = WEBHOOK_SORTS.get(message.etat)
    if not nom:
        return
    publier(
        nom,
        client_id=message.client_id,
        objet_id=message.id,
        donnees={
            "message_id": str(message.id),
            "etat": message.etat,
            "numero": message.numero_normalise,
            "reseau": message.operateur,
            "motif_echec": message.motif_echec or None,
            "horodatage": horodatage.isoformat(),
            "recu_le": message.date_reception.isoformat(),
            "environnement": "TEST",
        },
        cle_idempotence=f"webhook:essai:{message.id}:{message.etat}",
    )


def _etape_suivante(message: MessageEssai, maintenant) -> None:
    if message.etat == "RECU":
        message.etat = "SOUMIS"
        message.date_soumission = maintenant
        delai = DELAI_SANS_ACCUSE if message.issue == "SANS_ACCUSE" else DELAI_ACCUSE
        message.prochaine_etape_le = message.date_reception + delai
    else:
        message.etat = message.issue
        message.motif_echec = MOTIFS.get(message.issue, "")
        if message.issue != "SANS_ACCUSE":
            message.date_accuse = maintenant
        message.prochaine_etape_le = None
    message.historique = [
        *message.historique,
        {"etat": message.etat, "horodatage": maintenant.isoformat()},
    ]


def avancer(maintenant=None) -> int:
    """Fait avancer d'une étape les messages d'essai échus ; appelé à chaque
    tour de l'ordonnanceur. Un message en retard rattrape une étape par tour.
    Renvoie le nombre de messages avancés."""
    maintenant = maintenant or timezone.now()
    with transaction.atomic():
        lot = list(
            MessageEssai.objects.select_for_update(skip_locked=True)
            .filter(prochaine_etape_le__lte=maintenant)
            .order_by("prochaine_etape_le")[:LOT_AVANCE]
        )
        for message in lot:
            _etape_suivante(message, maintenant)
            _annoncer(message, maintenant)
        MessageEssai.objects.bulk_update(
            lot,
            [
                "etat",
                "date_soumission",
                "date_accuse",
                "motif_echec",
                "prochaine_etape_le",
                "historique",
            ],
        )
    return len(lot)


def purger(maintenant=None) -> int:
    """Efface les messages d'essai de plus de trente jours."""
    effaces, _ = MessageEssai.objects.filter(expire_le__lt=maintenant or timezone.now()).delete()
    return effaces
