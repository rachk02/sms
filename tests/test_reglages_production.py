"""Réglages de production : aucune valeur du poste de développement par défaut."""

import os
import subprocess
import sys

import pytest

from tests.test_separation_des_roles import ENV_WEB

LIRE = (
    "import django; django.setup(); from django.conf import settings;"
    " print(settings.MAILERS['default']['BACKEND']); print(settings.PORTAIL_URL)"
)


def _demarrer(**variables):
    environnement = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("POSTGRES_", "SMS_")) and k != "PORTAIL_URL"
    }
    environnement |= ENV_WEB | variables
    environnement = {k: v for k, v in environnement.items() if v is not None}
    return subprocess.run(
        [sys.executable, "-c", LIRE], capture_output=True, text=True, env=environnement
    )


def test_la_production_demarre_avec_un_vrai_moteur_et_un_portail_en_https():
    sortie = _demarrer()
    assert sortie.returncode == 0, sortie.stderr
    assert sortie.stdout.split() == [
        "django.core.mail.backends.smtp.EmailBackend",
        "https://sms.exemple.bf",
    ]


@pytest.mark.parametrize(
    "variables",
    [
        # Sans moteur, les courriels partiraient vers la console : les liens
        # d'invitation et de vérification, jetons compris, finiraient au journal.
        {"SMS_MOTEUR_COURRIEL": None},
        {"SMS_MOTEUR_COURRIEL": "django.core.mail.backends.console.EmailBackend"},
        {"SMS_MOTEUR_COURRIEL": "django.core.mail.backends.locmem.EmailBackend"},
        {"SMS_MOTEUR_COURRIEL": "django.core.mail.backends.filebased.EmailBackend"},
        {"SMS_MOTEUR_COURRIEL": "django.core.mail.backends.dummy.EmailBackend"},
        # Sans adresse, les liens des courriels mèneraient au poste de développement.
        {"PORTAIL_URL": None},
        {"PORTAIL_URL": "http://sms.exemple.bf"},
    ],
)
def test_la_production_refuse_de_demarrer_sur_une_valeur_de_developpement(variables):
    sortie = _demarrer(**variables)
    assert sortie.returncode != 0
    assert "ImproperlyConfigured" in sortie.stderr
