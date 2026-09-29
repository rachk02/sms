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

RACCROCHEES = ("default", "worker")


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
            connections[alias].close()
            connections[alias].settings_dict["NAME"] = nom
    yield
    with django_db_blocker.unblock():
        for alias in RACCROCHEES:
            connections[alias].close()
        teardown_databases(configuration, verbosity=verbosite)
