"""Réglages de production. Ce fichier ne lit que des variables d'environnement."""

from .base import *

# Jamais de paiement simulé en production, quoi que dise l'environnement.
PAIEMENTS_SIMULATEUR = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
