"""Réglages du poste de développement."""

from config.env import charger_fichier_env

charger_fichier_env()

from .base import *

DEBUG = True
