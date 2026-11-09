"""Réglages communs aux quatre environnements."""

import os
from urllib.parse import quote

from config.env import RACINE, env, env_liste

BASE_DIR = RACINE

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = False
ALLOWED_HOSTS = env_liste("DJANGO_ALLOWED_HOSTS")

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.postgres",
    "apps.core",
    "apps.comptes",
    "apps.fichiers",
    "apps.telecom",
    "apps.conformite",
    "apps.journal",
    "apps.credits",
    "apps.expediteurs",
    "apps.contacts",
    "apps.campagnes",
    "apps.paiements",
    "apps.support",
    "apps.api",
    "apps.backoffice",
]

AUTH_USER_MODEL = "comptes.Utilisateur"
AUTHENTICATION_BACKENDS = ["apps.comptes.authentification.AuthentificationVerrouillee"]
LOGIN_URL = "connexion"
LOGIN_REDIRECT_URL = "accueil"

# Une session expire après trente minutes sans activité : chaque requête la
# prolonge. Le cookie n'est lisible ni par le script de la page ni hors HTTPS
# en production (voir prod.py).
SESSION_COOKIE_AGE = 30 * 60
SESSION_SAVE_EVERY_REQUEST = True
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.JournalisationMiddleware",
    "apps.comptes.middleware.SecondFacteurMiddleware",
    "apps.core.middleware.CloisonnementMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]


def _base_de_donnees(role: str) -> dict:
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": env("POSTGRES_DB"),
        "USER": env(f"POSTGRES_{role}_USER"),
        "PASSWORD": env(f"POSTGRES_{role}_PASSWORD"),
        "HOST": env("POSTGRES_HOST"),
        "PORT": env("POSTGRES_PORT"),
    }


# Jusqu'à trois connexions vers la même base, avec trois rôles PostgreSQL :
#   default       le service web, rôle applicatif soumis au cloisonnement par
#                 ligne ; un worker pose SMS_CONNEXION_PAR_DEFAUT=WORKER avant de
#                 charger Django (voir workers/base.py) et prend le rôle worker
#   proprietaire  possède les tables et passe les migrations :
#                 manage.py migrate --database proprietaire
#   worker        les workers, qui contournent le cloisonnement
#   backoffice    le back-office des agents EL-IMMEKA, qui le contourne aussi
# Les trois dernières n'existent que si leurs identifiants sont fournis : le
# conteneur du service web ne reçoit que ceux du rôle applicatif.
DATABASES = {"default": _base_de_donnees(env("SMS_CONNEXION_PAR_DEFAUT", "APP"))}
for _alias, _role in (
    ("proprietaire", "PROPRIETAIRE"),
    ("worker", "WORKER"),
    ("backoffice", "BACKOFFICE"),
):
    if os.environ.get(f"POSTGRES_{_role}_USER"):
        DATABASES[_alias] = _base_de_donnees(_role)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.Argon2PasswordHasher",
]

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "fr-fr"
TIME_ZONE = "Africa/Ouagadougou"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

REDIS_URL = f"redis://{env('REDIS_HOST')}:{env('REDIS_PORT')}/{env('REDIS_DB', '0')}"
# Délai de connexion et de lecture, en secondes (décision A1 du 9 novembre 2026).
REDIS_DELAI_S = float(env("REDIS_DELAI_S", "2"))

RABBITMQ_USER = env("RABBITMQ_USER")
RABBITMQ_PASSWORD = env("RABBITMQ_PASSWORD")
# Hôte virtuel : « / » en développement, « recette » pour l'environnement de recette.
RABBITMQ_VHOST = env("RABBITMQ_VHOST", "/")
RABBITMQ_URL = (
    f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASSWORD}@{env('RABBITMQ_HOST')}:{env('RABBITMQ_PORT')}"
    f"/{quote(RABBITMQ_VHOST, safe='')}"
)

MINIO_BUCKET = env("MINIO_BUCKET", "sms")

# Page publique d'état du service (T-103) : dépôt derrière une abstraction,
# l'hébergeur n'est pas choisi. « dossier » écrit dans un dossier local servi en
# statique ; « seau » pousse vers un seau compatible S3, séparé de la plateforme.
PAGE_ETAT = {
    "depot": env("PAGE_ETAT_DEPOT", "dossier"),
    "dossier": env("PAGE_ETAT_DOSSIER", str(RACINE / "var" / "page-etat")),
    "point_acces": env("PAGE_ETAT_POINT_ACCES", ""),
    "identifiant": env("PAGE_ETAT_IDENTIFIANT", ""),
    "secret": env("PAGE_ETAT_SECRET", ""),
    "seau": env("PAGE_ETAT_SEAU", "page-etat"),
}
# Quotas de l'API publique par clé (T-107) : seau à jetons dans Redis, limite
# en rafale par seconde et limite soutenue par minute. Valeurs provisoires.
API_QUOTAS = {
    "rafale_par_seconde": int(env("API_QUOTA_RAFALE_PAR_SECONDE", "20")),
    "soutenu_par_minute": int(env("API_QUOTA_SOUTENU_PAR_MINUTE", "300")),
}
# Webhooks sortants (T-111) : un point de réception doit être public et en
# https. « 1 » accepte http et les adresses internes, pour le poste de
# développement et les tests seulement : jamais en production.
WEBHOOKS_ADRESSES_PRIVEES = env("WEBHOOKS_ADRESSES_PRIVEES", "") == "1"
STOCKAGE_OBJET = {
    "point_acces": env("MINIO_ENDPOINT"),
    "identifiant": env("MINIO_ROOT_USER"),
    "secret": env("MINIO_ROOT_PASSWORD"),
}

RABBITMQ_API_URL = f"http://{env('RABBITMQ_HOST')}:{env('RABBITMQ_CONSOLE_PORT')}/api"

# Jeton exigé par le point d'entrée des métriques, en en-tête
# « Authorization: Bearer <jeton> ». Vide : le point d'entrée refuse tout.
METRIQUES_JETON = env("METRIQUES_JETON", "")

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {"contexte": {"()": "apps.core.journalisation.FiltreContexte"}},
    "formatters": {"json": {"()": "apps.core.journalisation.FormatJson"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
            "filters": ["contexte"],
        }
    },
    "root": {"handlers": ["console"], "level": env("NIVEAU_JOURNAL", "INFO")},
}

# Passerelle Jasmin, lue par l'adaptateur des workers d'envoi. Ces identifiants
# sont ceux de l'API HTTP de notre Jasmin ; ceux des opérateurs restent dans la
# configuration de Jasmin, jamais dans la base.
JASMIN_HTTP_URL = f"http://{env('JASMIN_HOST', '127.0.0.1')}:{env('JASMIN_HTTP_PORT', '1401')}"
JASMIN_HTTP_USER = env("JASMIN_HTTP_USER", "")
JASMIN_HTTP_PASSWORD = env("JASMIN_HTTP_PASSWORD", "")
# Console d'administration de Jasmin, sondée par la surveillance des liaisons.
JASMIN_JCLI_HOST = env("JASMIN_HOST", "127.0.0.1")
JASMIN_JCLI_PORT = int(env("JASMIN_JCLI_PORT", "8990"))
JASMIN_JCLI_USER = env("JASMIN_JCLI_USER", "")
JASMIN_JCLI_PASSWORD = env("JASMIN_JCLI_PASSWORD", "")
# Adresse où Jasmin relaie les accusés, vue depuis le conteneur de Jasmin.
JASMIN_URL_RAPPEL = env("JASMIN_URL_RAPPEL", "")

# Durée de validité d'un SMS, transmise au SMSC : passé ce délai, l'opérateur
# ne tente plus de le livrer. La corrélation des accusés vit cette durée plus
# une marge (apps/telecom/correlation.py), 72 heures par défaut.
VALIDITE_SMS_HEURES = int(env("VALIDITE_SMS_HEURES", "48"))

# Secret des jetons portés par les adresses de rappel des passerelles.
RAPPELS_SECRET = env("RAPPELS_SECRET", "")

# Paiements (lot 7). Le fournisseur simulé tient la place d'Orange Money et de
# Moov Money tant qu'aucun contrat marchand n'existe ; il n'est jamais actif en
# production (prod.py le coupe). Sans secret ni plage d'adresses, tout rappel
# est refusé : aucune valeur réelle n'est écrite ici.
PAIEMENTS_SIMULATEUR = env("PAIEMENTS_SIMULATEUR", "") == "1"
PAIEMENTS_SIMULE_SECRET = env("PAIEMENTS_SIMULE_SECRET", "")
PAIEMENTS_PLAGES = {"simule": env_liste("PAIEMENTS_SIMULE_PLAGES", "")}
PAIEMENTS_RAPPELS_PAR_MINUTE = int(env("PAIEMENTS_RAPPELS_PAR_MINUTE", "60"))
# Frontaux dont on croit l'en-tête X-Forwarded-For. Vide : seule l'adresse de la
# connexion compte (voir apps/paiements/rappels.py).
PROXIES_DE_CONFIANCE = env_liste("PROXIES_DE_CONFIANCE", "")

# Identité d'EL-IMMEKA portée par chaque facture (T-88). Ces valeurs ne
# s'inventent pas : l'utilisateur les renseignera. Tant que l'une manque, aucune
# facture n'est émise ; les achats sont crédités et attendent leur facture.
FACTURATION_EMETTEUR = {
    "raison_sociale": env("FACTURATION_RAISON_SOCIALE", ""),
    "rccm": env("FACTURATION_RCCM", ""),
    "ifu": env("FACTURATION_IFU", ""),
    "regime_fiscal": env("FACTURATION_REGIME_FISCAL", ""),
    "adresse": env("FACTURATION_ADRESSE", ""),
}

# Espace servi par ce processus. « client » : le portail et l'API des clients,
# rôle applicatif, sans administration. « backoffice » : l'administration des
# agents EL-IMMEKA, joignable du seul réseau interne, avec
# SMS_CONNEXION_PAR_DEFAUT=BACKOFFICE.
ESPACE = env("SMS_ESPACE", "client")

# Courriels transactionnels. La console en développement ; le prestataire
# d'envoi est à choisir (point ouvert n°7 de l'architecture).
MAILERS = {
    "default": {
        "BACKEND": env("SMS_MOTEUR_COURRIEL", "django.core.mail.backends.console.EmailBackend")
    }
}
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "EL-IMMEKA SMS <ne-pas-repondre@el-immeka.bf>")
# Adresse publique du portail, pour les liens des courriels.
PORTAIL_URL = env("PORTAIL_URL", "http://127.0.0.1:8000").rstrip("/")

# Clé des secrets chiffrés en base (apps/core/chiffrement.py).
SMS_CLE_CHIFFREMENT = env("SMS_CLE_CHIFFREMENT", "")

# Prix de secours d'un segment, en FCFA entiers, quand aucune grille tarifaire
# publiée n'est en vigueur (voir apps/credits/tarifs.py). Absent hors du poste
# de développement et des tests : sans grille, un envoi est refusé.
SMS_PRIX_SEGMENT_PROVISOIRE = None

# Numéro court de la plateforme, factice jusqu'à son attribution par l'ARCEP
# (voir apps/telecom/numero_court.py).
SMS_NUMERO_COURT = env("SMS_NUMERO_COURT", "36000")
