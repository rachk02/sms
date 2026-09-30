"""Le worker d'import aiguille imports et exports de contacts d'une même file."""

import asyncio
import json
from unittest import mock

import pytest

from workers.import_contacts import WorkerImport


@pytest.mark.parametrize(
    "donnees, tache",
    [
        ({"import_id": "i-1"}, "apps.contacts.imports.traiter"),
        ({"export_id": "e-1"}, "apps.contacts.recherche.produire"),
    ],
)
def test_chaque_message_va_a_sa_tache(donnees, tache):
    message = mock.AsyncMock()
    message.body = json.dumps({"donnees": donnees}).encode()
    with mock.patch(tache) as appelee:
        asyncio.run(WorkerImport().traiter(message, mock.AsyncMock(), "import.contacts"))
    appelee.assert_called_once_with(next(iter(donnees.values())))
    message.ack.assert_awaited_once()
