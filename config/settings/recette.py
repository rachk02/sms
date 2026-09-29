"""Réglages de l'environnement de recette, isolé du développement.

Sa base, son hôte virtuel RabbitMQ et sa base Redis sont les siens, décrits
dans `.env.recette`, qui l'emporte sur `.env` pour toute variable qu'il pose.
Voir scripts/preparer_recette.sh.
"""

from config.env import RACINE, charger_fichier_env

charger_fichier_env(RACINE / ".env.recette")
charger_fichier_env()

from .base import *
