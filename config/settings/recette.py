"""Réglages de l'environnement de recette, isolé de la production."""

from config.env import charger_fichier_env

charger_fichier_env()

from .base import *
