from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.telecom.prefixes import FichierPrefixesInvalide, charger


class Command(BaseCommand):
    help = "Remplace la table des préfixes d'un pays par un fichier CSV (préfixe, opérateur)."

    def add_arguments(self, parser):
        parser.add_argument("fichier")
        parser.add_argument("--pays", default="BF")

    def handle(self, *args, fichier, pays, **options):
        try:
            nombre = charger(Path(fichier).read_text(encoding="utf-8"), pays)
        except FichierPrefixesInvalide as erreur:
            raise CommandError(str(erreur)) from None
        self.stdout.write(f"{nombre} préfixes chargés pour {pays}.")
