"""Contrôle des compteurs des campagnes terminées contre le journal (T-91).

    manage.py controler_compteurs                  # terminées depuis un jour
    manage.py controler_compteurs --jours 30
    manage.py controler_compteurs --campagne <uuid> --corriger

Sous le rôle du worker, qui voit toutes les campagnes. Sans `--corriger`, la
commande ne fait que lire et signaler ; elle sort en erreur si un écart existe.
"""

from django.core.management.base import BaseCommand, CommandError

from apps.campagnes.controle import controler, controler_terminees
from apps.campagnes.models import Campagne


class Command(BaseCommand):
    help = "Compare les compteurs des campagnes terminées au journal des messages."

    def add_arguments(self, parser):
        parser.add_argument("--jours", type=int, default=1)
        parser.add_argument("--campagne")
        parser.add_argument("--corriger", action="store_true")

    def handle(self, *args, jours, campagne, corriger, **options):
        if campagne:
            trouvee = Campagne.objects.filter(id=campagne).first()
            if trouvee is None:
                raise CommandError(f"Campagne {campagne} introuvable.")
            if not trouvee.terminee_le:
                raise CommandError(f"Campagne {campagne} pas encore terminée.")
            ecart = controler(trouvee, corriger=corriger)
            ecarts = [ecart] if ecart else []
        else:
            ecarts = controler_terminees(jours=jours, corriger=corriger)
        for ecart in ecarts:
            details = ", ".join(f"{c} {a} au lieu de {b}" for c, (a, b) in ecart.colonnes.items())
            suite = " (recomptée)" if ecart.corrige else ""
            self.stdout.write(f"{ecart.campagne_id} : {details}{suite}")
        if ecarts and not corriger:
            raise CommandError(f"{len(ecarts)} campagne(s) en écart avec le journal.")
        self.stdout.write(f"{len(ecarts)} écart(s).")
