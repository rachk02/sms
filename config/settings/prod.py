"""Réglages de production. Ce fichier ne lit que des variables d'environnement."""

from django.core.exceptions import ImproperlyConfigured

from .base import *

# Jamais de paiement simulé en production, quoi que dise l'environnement.
PAIEMENTS_SIMULATEUR = False
# Jamais de webhook vers une adresse interne en production (T-111).
WEBHOOKS_ADRESSES_PRIVEES = False

SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True

# Courriels et liens : aucune valeur du poste de développement par défaut. Sans
# moteur d'envoi, les courriels partiraient vers la console, donc au journal,
# avec les liens d'invitation et de vérification et leurs jetons ; sans adresse
# du portail, ces liens mèneraient au poste de développement. Le service refuse
# de démarrer plutôt que de servir ainsi.
MOTEURS_DE_DEVELOPPEMENT = {
    "django.core.mail.backends.console.EmailBackend",
    "django.core.mail.backends.locmem.EmailBackend",
    "django.core.mail.backends.filebased.EmailBackend",
    "django.core.mail.backends.dummy.EmailBackend",
}
MAILERS = {"default": {"BACKEND": env("SMS_MOTEUR_COURRIEL")}}
if MAILERS["default"]["BACKEND"] in MOTEURS_DE_DEVELOPPEMENT:
    raise ImproperlyConfigured(
        "SMS_MOTEUR_COURRIEL désigne un moteur de développement : interdit en production."
    )
PORTAIL_URL = env("PORTAIL_URL").rstrip("/")
if not PORTAIL_URL.startswith("https://"):
    raise ImproperlyConfigured("PORTAIL_URL doit être une adresse en https en production.")
