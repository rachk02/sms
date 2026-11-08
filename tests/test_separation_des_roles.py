"""Le service web ne détient que les identifiants du rôle applicatif."""

import json
import os
import subprocess
import sys

import pytest
from django.utils import timezone

from apps.core.redis import client_redis
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.supervision import CLE_LIAISONS, etat_publie, publier_etat_liaisons

ENV_WEB = {
    "DJANGO_SETTINGS_MODULE": "config.settings.prod",
    "DJANGO_SECRET_KEY": "x" * 50,
    "DJANGO_ALLOWED_HOSTS": "sms.exemple.bf",
    "POSTGRES_DB": "sms",
    "POSTGRES_HOST": "127.0.0.1",
    "POSTGRES_PORT": "5432",
    "POSTGRES_APP_USER": "sms_app",
    "POSTGRES_APP_PASSWORD": "secret-applicatif",
    "REDIS_HOST": "127.0.0.1",
    "REDIS_PORT": "6379",
    "RABBITMQ_USER": "sms",
    "RABBITMQ_PASSWORD": "secret",
    "RABBITMQ_HOST": "127.0.0.1",
    "RABBITMQ_PORT": "5672",
    "RABBITMQ_CONSOLE_PORT": "15672",
    "MINIO_ENDPOINT": "http://127.0.0.1:9000",
    "MINIO_ROOT_USER": "sms",
    "MINIO_ROOT_PASSWORD": "secret",
    "SMS_MOTEUR_COURRIEL": "django.core.mail.backends.smtp.EmailBackend",
    "PORTAIL_URL": "https://sms.exemple.bf",
}


def test_les_reglages_de_production_demarrent_avec_le_seul_role_applicatif():
    environnement = {k: v for k, v in os.environ.items() if not k.startswith(("POSTGRES_", "SMS_"))}
    environnement |= ENV_WEB
    sortie = subprocess.run(
        [
            sys.executable,
            "-c",
            "import django, json; django.setup(); from django.conf import settings;"
            " print(json.dumps({a: d['USER'] for a, d in settings.DATABASES.items()}))",
        ],
        capture_output=True,
        text=True,
        env=environnement,
        check=True,
    )
    assert json.loads(sortie.stdout) == {"default": "sms_app"}


@pytest.mark.django_db(transaction=True)
def test_l_etat_des_liaisons_publie_dans_redis_ne_porte_aucun_secret(en_tant_que_worker):
    connecteur = Connecteur.objects.create(
        nom=f"telecel-{timezone.now().timestamp()}",
        operateur=Operateur.TELECEL,
        type=Connecteur.Type.SMPP,
        identifiant="compte-smpp",
        secret_chiffre="ne-doit-jamais-sortir",
        etat_liaison=Connecteur.EtatLiaison.DEGRADE,
    )
    try:
        assert publier_etat_liaisons() >= 1
        liaisons, publiees_le = etat_publie()
        assert liaisons[str(connecteur.id)] == {
            "nom": connecteur.nom,
            "operateur": "TELECEL",
            "etat": "DEGRADE",
            "actif": True,
        }
        assert publiees_le is not None
        assert b"ne-doit-jamais-sortir" not in b"".join(client_redis().hvals(CLE_LIAISONS))
        assert 0 < client_redis().ttl(CLE_LIAISONS) <= 120
    finally:
        client_redis().delete(CLE_LIAISONS)
