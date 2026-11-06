"""Export du journal d'envois par le client, sans passer par le support (T-89)."""

import csv
import io
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.journal.models import Message
from apps.journal.services import enregistrer_message

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


def _message(client, texte, il_y_a):
    return enregistrer_message(
        client=client,
        numero_normalise="+22670123456",
        operateur="ORANGE",
        expediteur_libelle="BOUTIQUE",
        texte=texte,
        cout_unitaire=25,
        date_reception=timezone.now() - il_y_a,
        nature=Message.Nature.CLIENT,
    )


@pytest.fixture
def membre(en_tant_que_worker):
    boutique = creer_client(raison_sociale="Boutique SA", forme=Client.Forme.SOCIETE)
    banque = creer_client(raison_sociale="Banque SA", forme=Client.Forme.SOCIETE)
    _message(boutique, "Promotion de la semaine", timedelta(hours=5))
    _message(boutique, "Promotion du matin", timedelta(hours=7))
    _message(boutique, "Avant la période", timedelta(hours=50))
    _message(banque, "Votre relevé est disponible", timedelta(hours=2))
    return creer_utilisateur(
        courriel="u@boutique.bf",
        mot_de_passe="x-1234-abcd",
        nom="U",
        client=boutique,
        courriel_verifie_le=timezone.now(),
    )


def _lire(reponse):
    contenu = b"".join(reponse.streaming_content).decode("utf-8-sig")
    return list(csv.reader(io.StringIO(contenu), delimiter=";"))


def test_le_client_exporte_son_journal_sur_une_periode(client, membre):
    client.force_login(membre)
    aujourd_hui = timezone.localdate()
    reponse = client.get(
        "/journal/export",
        {"debut": f"{aujourd_hui - timedelta(days=1)}", "fin": f"{aujourd_hui}"},
    )
    assert reponse.status_code == 200 and reponse["Content-Type"].startswith("text/csv")
    entete, *lignes = _lire(reponse)
    assert entete[:2] == ["date_reception", "numero"]
    textes = [ligne[entete.index("texte")] for ligne in lignes]
    # Les siens seulement, dans la période, du plus ancien au plus récent.
    assert textes == ["Promotion du matin", "Promotion de la semaine"]


@pytest.mark.parametrize(
    "periode",
    [
        {},
        {"debut": "2026-01-01"},
        {"debut": "2026-05-01", "fin": "2026-04-01"},
        {"debut": "2025-01-01", "fin": "2026-06-01"},
    ],
    ids=["sans dates", "sans fin", "a l envers", "plus d un an"],
)
def test_une_periode_absente_ou_trop_longue_est_refusee(client, membre, periode):
    client.force_login(membre)
    assert client.get("/journal/export", periode).status_code == 400
