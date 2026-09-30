from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from apps.expediteurs.services import FichierReservesInvalide, charger_reserves

INITIAL = Path(__file__).resolve().parents[2] / "donnees" / "reserves-initiales.csv"


class Command(BaseCommand):
    help = (
        "Ajoute ou met à jour les noms réservés depuis un fichier CSV (libellé;motif). "
        "Sans fichier, charge la liste initiale. À lancer avec la connexion du back-office "
        "(SMS_CONNEXION_PAR_DEFAUT=BACKOFFICE)."
    )

    def add_arguments(self, parser):
        parser.add_argument("fichier", nargs="?", default=str(INITIAL))

    def handle(self, *args, fichier, **options):
        try:
            nombre = charger_reserves(Path(fichier).read_text(encoding="utf-8"))
        except FichierReservesInvalide as erreur:
            raise CommandError(str(erreur)) from None
        self.stdout.write(f"{nombre} noms réservés chargés.")
