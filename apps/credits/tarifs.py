"""Prix d'un segment, en FCFA entiers, lu dans les grilles tarifaires datées.

Une grille ne s'applique que par une version publiée en vigueur au moment `a`
(par défaut, maintenant). Dans chaque version, pour le pays du client, le
réseau et le volume mensuel donné, le palier retenu est celui du plus grand
volume minimal atteint.

Règle de priorité, réseau par réseau (T-83, décision du 6 novembre 2026) :

1. si la grille négociée du client ou une promotion qui le vise (ouverte à
   tous ou réservée à lui) tarifie ce réseau, le client paie le plus bas de
   ces prix ; à prix égal, la grille négociée l'emporte ;
2. sinon, la grille publique ;
3. sinon, refus (AucunTarif), sauf si aucune grille publique n'est en vigueur
   et que `SMS_PRIX_SEGMENT_PROVISOIRE` est posé : ce prix de secours n'existe
   qu'en développement et dans les tests, jamais en production.

La grille publique n'entre pas dans la comparaison : un prix négocié plus haut
que le prix public reste le prix du client, jusqu'à ce que sa grille change.
"""

from dataclasses import dataclass

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.comptes.models import Client

from .models import Grille, PalierGrille, VersionGrille

Nature = Grille.Nature


class AucunTarif(ValueError):
    def __init__(self, operateur: str, pays: str):
        self.operateur, self.pays = operateur, pays
        super().__init__(f"Aucun tarif en vigueur pour le réseau {operateur} ({pays}).")


@dataclass(frozen=True)
class Tarif:
    prix: int
    # Version de grille appliquée et sa nature ; None pour le prix de secours.
    version_id: object
    nature: str | None
    # Volume minimal du palier retenu.
    palier: int


def _en_vigueur(a) -> Q:
    return Q(version__publiee=True, version__debut__lte=a) & (
        Q(version__fin__isnull=True) | Q(version__fin__gt=a)
    )


def _visibles(client_id) -> Q:
    """Les grilles qui peuvent tarifer ce client. Le cloisonnement le garantit
    déjà sous le rôle applicatif ; les workers le voient écrit ici."""
    return (
        Q(version__grille__nature=Nature.PUBLIQUE)
        | Q(version__grille__nature=Nature.NEGOCIEE, version__grille__client_id=client_id)
        | Q(version__grille__nature=Nature.PROMOTION, version__grille__client__isnull=True)
        | Q(version__grille__nature=Nature.PROMOTION, version__grille__client_id=client_id)
    )


def version_en_vigueur(a=None) -> VersionGrille | None:
    """La version publiée de la grille publique en vigueur à `a`."""
    a = a or timezone.now()
    return (
        VersionGrille.objects.filter(grille__nature=Nature.PUBLIQUE, publiee=True, debut__lte=a)
        .filter(Q(fin__isnull=True) | Q(fin__gt=a))
        .first()
    )


def candidats(client_id, pays: str, operateur: str, *, a, volume: int) -> list[Tarif]:
    """Un tarif par version en vigueur qui tarifie ce réseau pour ce client, à
    son palier atteint."""
    lignes = (
        PalierGrille.objects.filter(
            _en_vigueur(a), _visibles(client_id), pays=pays, operateur=operateur
        )
        .filter(volume_min__lte=volume)
        .values_list("version_id", "version__grille__nature", "volume_min", "prix_unitaire")
        .order_by("version_id", "-volume_min")
    )
    retenus = {}
    for version_id, nature, palier, prix in lignes:
        retenus.setdefault(version_id, Tarif(prix, version_id, nature, palier))
    return list(retenus.values())


ORDRE = {Nature.NEGOCIEE: 0, Nature.PROMOTION: 1}


def tarif(client_id, operateur: str, *, a=None, volume: int = 0) -> Tarif:
    """Le prix d'un segment et d'où il vient : version, nature et palier."""
    a = a or timezone.now()
    pays = Client.objects.values_list("pays", flat=True).get(id=client_id)
    tous = candidats(client_id, pays, operateur, a=a, volume=volume)
    speciaux = [t for t in tous if t.nature != Nature.PUBLIQUE]
    if speciaux:
        return min(speciaux, key=lambda t: (t.prix, ORDRE[t.nature], str(t.version_id)))
    publics = [t for t in tous if t.nature == Nature.PUBLIQUE]
    if publics:
        return publics[0]
    if settings.SMS_PRIX_SEGMENT_PROVISOIRE is not None and version_en_vigueur(a) is None:
        return Tarif(settings.SMS_PRIX_SEGMENT_PROVISOIRE, None, None, 0)
    raise AucunTarif(operateur, pays)


def prix_segment(client_id, operateur: str, *, a=None, volume: int = 0) -> int:
    return tarif(client_id, operateur, a=a, volume=volume).prix
