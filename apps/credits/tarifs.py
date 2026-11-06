"""Prix d'un segment, en FCFA entiers, lu dans les grilles tarifaires datées.

Le prix est celui de la version publiée en vigueur au moment `a` (par défaut,
maintenant), pour le pays du client, le réseau et le volume mensuel donné : le
palier retenu est celui du plus grand volume minimal atteint. Une grille sans
version publiée n'est jamais appliquée.

Sans aucune version publiée en vigueur, le prix est refusé (AucunTarif), sauf
si `SMS_PRIX_SEGMENT_PROVISOIRE` est posé : ce prix de secours n'existe qu'en
développement et dans les tests, jamais en production (décision du 6 novembre
2026). Une version publiée qui ne tarife pas le réseau refuse toujours.

T-81 ne lit que la grille publique ; la priorité entre grille négociée, grille
promotionnelle et grille publique vient avec T-83.
"""

from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from apps.comptes.models import Client

from .models import PalierGrille, VersionGrille


class AucunTarif(ValueError):
    def __init__(self, operateur: str, pays: str):
        self.operateur, self.pays = operateur, pays
        super().__init__(f"Aucun tarif en vigueur pour le réseau {operateur} ({pays}).")


def version_en_vigueur(a=None) -> VersionGrille | None:
    """La version publiée de la grille publique en vigueur à `a`."""
    a = a or timezone.now()
    return (
        VersionGrille.objects.filter(grille__client__isnull=True, publiee=True, debut__lte=a)
        .filter(Q(fin__isnull=True) | Q(fin__gt=a))
        .first()
    )


def prix_segment(client_id, operateur: str, *, a=None, volume: int = 0) -> int:
    pays = Client.objects.values_list("pays", flat=True).get(id=client_id)
    version = version_en_vigueur(a)
    if version is None:
        if settings.SMS_PRIX_SEGMENT_PROVISOIRE is not None:
            return settings.SMS_PRIX_SEGMENT_PROVISOIRE
        raise AucunTarif(operateur, pays)
    palier = (
        PalierGrille.objects.filter(
            version=version, pays=pays, operateur=operateur, volume_min__lte=volume
        )
        .order_by("-volume_min")
        .first()
    )
    if palier is None:
        raise AucunTarif(operateur, pays)
    return palier.prix_unitaire
