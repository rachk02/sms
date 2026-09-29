"""Tests du socle : configuration et réglages communs."""

import pytest
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.core.management import call_command

from config.env import env


def test_les_controles_django_passent():
    call_command("check")


def test_argon2_est_le_seul_condensat_de_mot_de_passe():
    assert settings.PASSWORD_HASHERS == ["django.contrib.auth.hashers.Argon2PasswordHasher"]


def test_le_fuseau_est_celui_de_ouagadougou():
    assert settings.TIME_ZONE == "Africa/Ouagadougou"
    assert settings.USE_TZ is True


def test_une_variable_absente_sans_defaut_est_refusee(monkeypatch):
    monkeypatch.delenv("VARIABLE_QUI_N_EXISTE_PAS", raising=False)
    with pytest.raises(ImproperlyConfigured, match="VARIABLE_QUI_N_EXISTE_PAS"):
        env("VARIABLE_QUI_N_EXISTE_PAS")
