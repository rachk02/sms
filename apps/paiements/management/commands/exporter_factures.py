"""Export comptable des factures et avoirs d'une période, en CSV (T-88).

    manage.py exporter_factures --debut 2026-11-01 --fin 2026-12-01 > factures.csv

La période est obligatoire, fin exclue. Lecture par lots, pagination par
curseur sur (emise_le, id) : jamais d'OFFSET. Sous le rôle du back-office ou
du worker, qui voient toutes les factures.
"""

import csv
from datetime import datetime, time

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date

from apps.core.tableur import cellules
from apps.paiements.models import Facture

COLONNES = (
    "numero",
    "type",
    "emise_le",
    "client",
    "ifu_client",
    "montant_ht",
    "taux_tva",
    "tva",
    "montant_ttc",
    "facture_d_origine",
    "moyen_de_paiement",
    "reference_de_paiement",
)
LOT = 500


def _jour(valeur: str, option: str):
    jour = parse_date(valeur or "")
    if jour is None:
        raise CommandError(f"{option} attend une date AAAA-MM-JJ.")
    return timezone.make_aware(datetime.combine(jour, time.min))


def lignes(debut, fin):
    """Les factures de [debut, fin), dans l'ordre d'émission, lot par lot."""
    curseur = None
    while True:
        lot = Facture.objects.filter(emise_le__gte=debut, emise_le__lt=fin)
        if curseur:
            lot = lot.filter(Q(emise_le__gt=curseur[0]) | Q(emise_le=curseur[0], id__gt=curseur[1]))
        lot = list(lot.order_by("emise_le", "id")[:LOT])
        if not lot:
            return
        for facture in lot:
            mentions = facture.mentions
            paiement = mentions.get("paiement") or {}
            yield (
                facture.numero,
                facture.type,
                timezone.localtime(facture.emise_le).isoformat(timespec="seconds"),
                mentions["client"]["raison_sociale"],
                mentions["client"].get("ifu", ""),
                facture.montant_ht,
                facture.taux_tva,
                facture.tva,
                facture.montant_ttc,
                (mentions.get("origine") or {}).get("numero", ""),
                paiement.get("moyen", ""),
                paiement.get("reference", ""),
            )
        curseur = (lot[-1].emise_le, lot[-1].id)


class Command(BaseCommand):
    help = "Exporte en CSV les factures et avoirs émis sur une période."

    def add_arguments(self, parser):
        parser.add_argument("--debut", required=True, help="premier jour, AAAA-MM-JJ")
        parser.add_argument("--fin", required=True, help="lendemain du dernier jour, AAAA-MM-JJ")

    def handle(self, *args, debut, fin, **options):
        debut, fin = _jour(debut, "--debut"), _jour(fin, "--fin")
        if fin <= debut:
            raise CommandError("--fin doit suivre --debut.")
        # self.stdout ajoute lui-même la fin de ligne à chaque écriture.
        sortie = csv.writer(self.stdout, delimiter=";", lineterminator="")
        sortie.writerow(COLONNES)
        for ligne in lignes(debut, fin):
            sortie.writerow(cellules(ligne))
