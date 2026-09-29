"""Jeu d'essai de l'environnement de recette. Idempotent.

Un client par palier, chacun approvisionné, et un connecteur simulé par
opérateur, avec des délais et des taux d'échec différents. Le jeu de données
volontairement sale de la fiche F-42 (imports avec doublons, numéros à sept
chiffres, caractères hors table GSM) arrive avec les contacts, au lot 4.
Refuse de tourner ailleurs qu'en recette.
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.cloisonnement import poser_client
from apps.credits import services as credits
from apps.telecom.models import Connecteur, Operateur

CLIENTS = (
    ("Recette Découverte", Client.Palier.DECOUVERTE, 5_000),
    ("Recette Vérifié", Client.Palier.VERIFIE, 500_000),
    ("Recette Contractuel", Client.Palier.CONTRACTUEL, 50_000_000),
)

CONNECTEURS = (
    ("recette-orange", Operateur.ORANGE, {"delai_accuse_s": 2, "taux_echec": 0.03}),
    ("recette-moov", Operateur.MOOV, {"delai_accuse_s": 5, "taux_echec": 0.05}),
    ("recette-telecel", Operateur.TELECEL, {"delai_accuse_s": 10, "taux_echec": 0.08}),
)


class Command(BaseCommand):
    help = "Crée le jeu d'essai de la recette : clients par palier et connecteurs simulés."

    def handle(self, *args, **options):
        if not settings.SETTINGS_MODULE.endswith(".recette"):
            raise CommandError("Réservé à config.settings.recette.")
        for nom, operateur, parametres in CONNECTEURS:
            Connecteur.objects.using("worker").update_or_create(
                nom=nom,
                defaults={
                    "operateur": operateur,
                    "type": Connecteur.Type.SIMULE,
                    "parametres": parametres,
                    "etat_liaison": Connecteur.EtatLiaison.CONNECTE,
                },
            )
        for raison_sociale, palier, approvisionnement in CLIENTS:
            client = Client.objects.using("worker").filter(
                raison_sociale=raison_sociale
            ).first() or creer_client(
                raison_sociale=raison_sociale, forme=Client.Forme.SOCIETE, palier=palier
            )
            with transaction.atomic():
                poser_client(client.id)
                if credits.solde(client.id).acquis == 0:
                    credits.acheter(client.id, approvisionnement, reference=f"RECETTE-{palier}")
        self.stdout.write(
            f"{len(CLIENTS)} clients et {len(CONNECTEURS)} connecteurs simulés prêts."
        )
