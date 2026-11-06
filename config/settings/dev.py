"""Réglages du poste de développement."""

from config.env import charger_fichier_env

charger_fichier_env()

from .base import *

DEBUG = True

# Prix de secours tant qu'aucune grille n'est publiée sur le poste ; jamais en
# recette ni en production (voir apps/credits/tarifs.py).
SMS_PRIX_SEGMENT_PROVISOIRE = int(env("SMS_PRIX_SEGMENT_PROVISOIRE", "20"))

# Fournisseur de paiement simulé, rappels acceptés depuis le poste lui-même.
PAIEMENTS_SIMULATEUR = True
PAIEMENTS_PLAGES = {"simule": env_liste("PAIEMENTS_SIMULE_PLAGES", "127.0.0.1/32")}
