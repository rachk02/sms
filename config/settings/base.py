"""Réglages communs aux quatre environnements."""

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
]

AUTH_USER_MODEL = "comptes.Utilisateur"

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
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


# Trois connexions vers la même base, avec trois rôles PostgreSQL distincts.
# `proprietaire` vient en premier : c'est lui qui crée la base de test et y
# lance les migrations, les deux autres s'y raccrochent.
#   proprietaire  possède les tables, lance les migrations :
#                 manage.py migrate --database proprietaire
#   default       le service web, soumis au cloisonnement par ligne
#   worker        les workers, qui contournent le cloisonnement
DATABASES = {
    "proprietaire": _base_de_donnees("PROPRIETAIRE"),
    "default": _base_de_donnees("APP"),
    "worker": _base_de_donnees("WORKER"),
}

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

REDIS_URL = f"redis://{env('REDIS_HOST')}:{env('REDIS_PORT')}/0"

RABBITMQ_URL = (
    f"amqp://{env('RABBITMQ_USER')}:{env('RABBITMQ_PASSWORD')}"
    f"@{env('RABBITMQ_HOST')}:{env('RABBITMQ_PORT')}/"
)

STOCKAGE_OBJET = {
    "point_acces": env("MINIO_ENDPOINT"),
    "identifiant": env("MINIO_ROOT_USER"),
    "secret": env("MINIO_ROOT_PASSWORD"),
}
