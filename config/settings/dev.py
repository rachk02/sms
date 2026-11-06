"""Réglages du poste de développement."""

from config.env import charger_fichier_env

charger_fichier_env()

from .base import *

DEBUG = True

# Prix de secours tant qu'aucune grille n'est publiée sur le poste ; jamais en
# recette ni en production (voir apps/credits/tarifs.py).
SMS_PRIX_SEGMENT_PROVISOIRE = int(env("SMS_PRIX_SEGMENT_PROVISOIRE", "20"))
