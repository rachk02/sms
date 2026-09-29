"""Configuration commune des tests.

Django crée la base de test par la connexion `default`, qui est ici le rôle
applicatif : il n'a ni le droit de créer une base ni celui de posséder des
tables, et c'est voulu. La base de test est donc créée et migrée par la
connexion `proprietaire`, comme en exploitation, puis `default` et `worker` y
sont raccrochées avec leurs propres rôles.
"""

import pytest
from django.db import connections
from django.test.utils import setup_databases, teardown_databases

RACCROCHEES = ("default", "worker", "backoffice")


@pytest.fixture(scope="session")
def django_db_setup(request, django_test_environment, django_db_blocker):
    verbosite = request.config.option.verbose
    with django_db_blocker.unblock():
        configuration = setup_databases(
            verbosity=verbosite,
            interactive=False,
            aliases={"proprietaire"},
            serialized_aliases=set(),
        )
        nom = connections["proprietaire"].settings_dict["NAME"]
        for alias in RACCROCHEES:
            if alias in connections:
                connections[alias].close()
                connections[alias].settings_dict["NAME"] = nom
    yield
    with django_db_blocker.unblock():
        for alias in RACCROCHEES:
            if alias in connections:
                connections[alias].close()
        teardown_databases(configuration, verbosity=verbosite)


def _en_tant_que(alias):
    """Le temps d'un test, la connexion `default` prend le rôle d'une autre
    connexion, comme dans le processus qui la reçoit par défaut."""
    reglages = connections["default"].settings_dict
    avant = reglages["USER"], reglages["PASSWORD"]
    autre = connections[alias].settings_dict
    connections["default"].close()
    reglages["USER"], reglages["PASSWORD"] = autre["USER"], autre["PASSWORD"]
    yield
    connections["default"].close()
    reglages["USER"], reglages["PASSWORD"] = avant


@pytest.fixture
def en_tant_que_worker(transactional_db):
    """Rôle des workers, comme dans un processus lancé par `workers.base.lancer`."""
    yield from _en_tant_que("worker")


@pytest.fixture
def en_tant_que_backoffice(transactional_db, settings):
    """Rôle et espace du back-office des agents EL-IMMEKA."""
    from django.urls import clear_url_caches

    settings.ESPACE = "backoffice"
    import importlib

    import config.urls

    importlib.reload(config.urls)
    clear_url_caches()
    yield from _en_tant_que("backoffice")
    settings.ESPACE = "client"
    importlib.reload(config.urls)
    clear_url_caches()
