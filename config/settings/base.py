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
    "apps.core",
    "apps.comptes",
    "apps.telecom",
    "apps.journal",
    "apps.credits",
    "apps.backoffice",
]

AUTH_USER_MODEL = "comptes.Utilisateur"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.core.middleware.JournalisationMiddleware",
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
# Les deux dernières n'existent que si leurs identifiants sont fournis : le
# conteneur du service web ne reçoit que ceux du rôle applicatif.
DATABASES = {"default": _base_de_donnees(env("SMS_CONNEXION_PAR_DEFAUT", "APP"))}
for _alias, _role in (("proprietaire", "PROPRIETAIRE"), ("worker", "WORKER")):
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

REDIS_URL = f"redis://{env('REDIS_HOST')}:{env('REDIS_PORT')}/{env('REDIS_DB', '0')}"

RABBITMQ_USER = env("RABBITMQ_USER")
RABBITMQ_PASSWORD = env("RABBITMQ_PASSWORD")
# Hôte virtuel : « / » en développement, « recette » pour l'environnement de recette.
RABBITMQ_VHOST = env("RABBITMQ_VHOST", "/")
RABBITMQ_URL = (
    f"amqp://{RABBITMQ_USER}:{RABBITMQ_PASSWORD}@{env('RABBITMQ_HOST')}:{env('RABBITMQ_PORT')}"
    f"/{quote(RABBITMQ_VHOST, safe='')}"
)

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
# Adresse où Jasmin relaie les accusés, vue depuis le conteneur de Jasmin.
JASMIN_URL_RAPPEL = env("JASMIN_URL_RAPPEL", "")

# Durée de validité d'un SMS, transmise au SMSC : passé ce délai, l'opérateur
# ne tente plus de le livrer. La corrélation des accusés vit cette durée plus
# une marge (apps/telecom/correlation.py), 72 heures par défaut.
VALIDITE_SMS_HEURES = int(env("VALIDITE_SMS_HEURES", "48"))
