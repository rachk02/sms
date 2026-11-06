"""Publie une version de grille tarifaire depuis un fichier CSV.

    manage.py publier_grille prix.csv --par admin@el-immeka.bf
        [--nature PUBLIQUE|NEGOCIEE|PROMOTION] [--client <uuid>] [--nom ...]
        [--debut 2026-12-01T00:00] [--fin 2026-12-31T23:59]

Le fichier porte une ligne d'en-tête `pays,operateur,volume_min,prix_unitaire`
puis une ligne par palier, prix en FCFA entiers hors taxes. Se lance sous le
rôle du back-office, au nom d'un super-administrateur, seul habilité à publier :
la publication va au journal d'audit comme depuis toute autre porte. Tout ou
rien : une erreur ne laisse aucun brouillon derrière elle.
"""

import csv
import io
from datetime import datetime
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from apps.comptes.models import GestionnaireUtilisateur, Utilisateur
from apps.credits import services
from apps.credits.models import Grille

COLONNES = ["pays", "operateur", "volume_min", "prix_unitaire"]


def _date(valeur: str | None):
    if not valeur:
        return None
    date = datetime.fromisoformat(valeur)
    return timezone.make_aware(date) if timezone.is_naive(date) else date


def lire_paliers(texte: str) -> list[dict]:
    lecteur = csv.DictReader(io.StringIO(texte))
    if lecteur.fieldnames != COLONNES:
        raise CommandError(f"En-tête attendu : {','.join(COLONNES)}")
    paliers = []
    for numero, ligne in enumerate(lecteur, start=2):
        try:
            paliers.append(
                {
                    "pays": ligne["pays"].strip(),
                    "operateur": ligne["operateur"].strip(),
                    "volume_min": int(ligne["volume_min"]),
                    "prix_unitaire": int(ligne["prix_unitaire"]),
                }
            )
        except TypeError, ValueError:
            raise CommandError(f"Ligne {numero} : volume et prix en entiers.") from None
    if not paliers:
        raise CommandError("Aucun palier dans le fichier.")
    return paliers


class Command(BaseCommand):
    help = "Publie une version de grille tarifaire (CSV : pays, opérateur, volume, prix)."

    def add_arguments(self, parser):
        parser.add_argument("fichier")
        parser.add_argument("--par", required=True, help="courriel du super-administrateur")
        parser.add_argument(
            "--nature", default=Grille.Nature.PUBLIQUE, choices=Grille.Nature.values
        )
        parser.add_argument("--client", default=None)
        parser.add_argument("--nom", default=None)
        parser.add_argument("--debut", default=None)
        parser.add_argument("--fin", default=None)

    def handle(self, *args, fichier, par, nature, client, nom, debut, fin, **options):
        paliers = lire_paliers(Path(fichier).read_text(encoding="utf-8"))
        courriel = GestionnaireUtilisateur.normaliser_courriel(par)
        auteur = Utilisateur.objects.filter(courriel=courriel).first()
        if auteur is None:
            raise CommandError(f"Aucun utilisateur {par}.")
        try:
            with transaction.atomic():
                grille = self._grille(nature, client, nom, auteur)
                version = services.creer_version(grille, debut=_date(debut), fin=_date(fin))
                for palier in paliers:
                    services.fixer_palier(version, **palier)
                version = services.publier_version(version, auteur=auteur)
        except (services.TarifInvalide, services.PublicationNonAutorisee) as erreur:
            raise CommandError(str(erreur)) from None
        self.stdout.write(
            f"{grille.nom} : {len(paliers)} palier(s) publiés, en vigueur le "
            f"{timezone.localtime(version.debut):%d/%m/%Y %H:%M}."
        )

    def _grille(self, nature, client, nom, auteur) -> Grille:
        if nature == Grille.Nature.PROMOTION:
            return services.creer_grille(
                nom or "Promotion", auteur=auteur, nature=nature, client_id=client
            )
        existante = Grille.objects.filter(nature=nature, client_id=client).first()
        if existante:
            return existante
        defaut = "Grille publique" if nature == Grille.Nature.PUBLIQUE else "Grille négociée"
        return services.creer_grille(nom or defaut, auteur=auteur, nature=nature, client_id=client)
