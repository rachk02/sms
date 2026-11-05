"""Trois mille messages lissés sur trois heures (T-66), mesurés sur les
heures de soumission.

L'horloge est simulée : chaque tour de l'ordonnanceur avance d'une minute, et
le worker soumet ce qui vient d'être publié à l'heure simulée. Environ quatre
minutes, à cause des trois mille soumissions réelles.
"""

from collections import Counter
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.campagnes import diffusion
from apps.campagnes.tests.test_lissage import (  # noqa: F401 (fixtures)
    Publies,
    _confirmee,
    _connecteur,
    client,
    reglages,
)
from apps.journal.models import Message
from workers.envoi import WorkerEnvoi

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("reglages"),
]


def test_trois_mille_messages_lisses_sur_trois_heures_font_environ_mille_par_heure(
    client,  # noqa: F811 (fixture importée)
    monkeypatch,
):
    _connecteur(debit=100)
    campagne = _confirmee(client, 3_000, lissage_heures=3)
    assert campagne.lissage_par_minute == 17  # 3 000 sur 180 minutes, arrondi au-dessus
    debut = timezone.now()
    horloge = [debut]
    monkeypatch.setattr(timezone, "now", lambda: horloge[0])
    worker, publies = WorkerEnvoi(files=[]), Publies()
    while horloge[0] < debut + timedelta(hours=3, minutes=10):
        diffusion.publier(maintenant=horloge[0])
        for donnees in publies.nouveaux():
            worker._soumettre(donnees)  # soumis à l'heure simulée
        horloge[0] += timedelta(minutes=1)  # un tour par palier
    soumissions = Message.objects.filter(campagne_id=campagne.id).values_list(
        "date_soumission", flat=True
    )
    par_heure = Counter(int((s - debut).total_seconds() // 3600) for s in soumissions)
    assert sum(par_heure.values()) == 3_000
    assert sorted(par_heure) == [0, 1, 2]  # fini dans les trois heures
    assert all(900 <= par_heure[h] <= 1_100 for h in range(3)), par_heure
    # Par paliers réguliers : jamais plus d'une minute de messages d'un coup.
    par_minute = Counter(int((s - debut).total_seconds() // 60) for s in soumissions)
    assert max(par_minute.values()) <= 17
