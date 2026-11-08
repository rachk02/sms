"""Bac à sable des intégrateurs (T-113).

Un intégrateur teste les cas de livraison, d'échec et de sans accusé sans
dépenser un franc. Les réponses ont exactement la même forme qu'en production.
Aucun message ne part vers un vrai numéro depuis le bac à sable.
"""

import json
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.api import essais
from apps.api.models import MessageEssai
from apps.backoffice.tests.test_clients import _preparer
from apps.comptes.models import CleApi
from apps.comptes.services import creer_cle
from apps.core.models import EvenementSortant
from apps.credits import services as credits
from apps.credits.models import Ecriture, Reservation
from apps.journal.models import Message
from apps.telecom.models import Prefixe

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

ENVOI = "/v1/messages"


@pytest.fixture
def cles(espaces, settings):
    settings.SMS_PRIX_SEGMENT_PROVISOIRE = 20
    settings.SMS_NUMERO_COURT = "36000"
    espaces("worker")
    client = _preparer()
    _, essai = creer_cle(
        client=client, nom="Essais", auteur=None, environnement=CleApi.Environnement.TEST
    )
    _, reelle = creer_cle(client=client, nom="Site", auteur=None)
    espaces("client")
    yield (
        client,
        {"HTTP_AUTHORIZATION": f"Bearer {essai}"},
        {"HTTP_AUTHORIZATION": f"Bearer {reelle}"},
    )
    espaces("worker")
    Prefixe.objects.filter(pays="BF").delete()


def _poster(client, entetes, corps, cle=None):
    if cle:
        entetes = {**entetes, "HTTP_IDEMPOTENCY_KEY": cle}
    return client.post(ENVOI, json.dumps(corps), content_type="application/json", **entetes)


def _lot(*fins):
    return {"messages": [{"numero": f"7000000{fin}", "texte": "Bonjour"} for fin in fins]}


def test_un_essai_ne_part_pas_et_ne_coute_rien(client, cles, espaces):
    compte, essai, _ = cles
    espaces("worker")
    avant = credits.solde(compte.id)
    ecritures = Ecriture.objects.count()
    espaces("client")
    reponse = _poster(client, essai, _lot(1, 7, 8, 9))
    assert reponse.status_code == 202 and reponse.json()["acceptes"] == 4
    espaces("worker")
    # Ni journal, ni réservation, ni écriture, ni file d'envoi, ni plafond.
    assert not Message.objects.exists() and not Reservation.objects.exists()
    assert Ecriture.objects.count() == ecritures
    assert credits.solde(compte.id) == avant
    assert not EvenementSortant.objects.filter(nom__startswith="envoi.").exists()
    assert MessageEssai.objects.filter(client=compte).count() == 4


def test_les_reponses_ont_la_meme_forme_qu_en_production(client, cles):
    _, essai, reelle = cles
    corps = {
        "messages": [{"numero": "70000001", "texte": "Bonjour"}, {"numero": "abc", "texte": "x"}]
    }
    de_essai = _poster(client, essai, corps).json()
    de_prod = _poster(client, reelle, corps).json()
    assert de_essai.keys() == de_prod.keys()
    for a, b in zip(de_essai["messages"], de_prod["messages"], strict=True):
        assert a.keys() == b.keys()
    assert de_essai["messages"][1]["erreur"] == de_prod["messages"][1]["erreur"]
    # La lecture aussi : statut d'un message et journal.
    essai_id, prod_id = de_essai["messages"][0]["id"], de_prod["messages"][0]["id"]
    fiche_essai = client.get(f"{ENVOI}/{essai_id}", **essai).json()
    fiche_prod = client.get(f"{ENVOI}/{prod_id}", **reelle).json()
    assert fiche_essai.keys() == fiche_prod.keys()
    journal_essai = client.get(ENVOI, **essai).json()
    assert journal_essai.keys() == client.get(ENVOI, **reelle).json().keys()
    assert [m["id"] for m in journal_essai["messages"]] == [essai_id]
    # Chaque environnement ne voit que ses messages.
    assert client.get(f"{ENVOI}/{prod_id}", **essai).status_code == 404
    assert client.get(f"{ENVOI}/{essai_id}", **reelle).status_code == 404


def test_le_dernier_chiffre_choisit_l_accuse_simule(client, cles, espaces):
    compte, essai, _ = cles
    ids = [m["id"] for m in _poster(client, essai, _lot(3, 7, 8, 9)).json()["messages"]]
    espaces("worker")
    debut = timezone.now()
    # Une seconde : soumis ; quatre : accusés ; vingt : sans accusé.
    assert essais.avancer(debut + timedelta(seconds=2)) == 4
    assert essais.avancer(debut + timedelta(seconds=5)) == 3
    assert essais.avancer(debut + timedelta(seconds=25)) == 1
    assert essais.avancer(debut + timedelta(seconds=60)) == 0
    espaces("client")
    etats = [client.get(f"{ENVOI}/{i}", **essai).json() for i in ids]
    assert [e["etat"] for e in etats] == ["LIVRE", "NON_LIVRE", "EXPIRE", "SANS_ACCUSE"]
    assert [e["motif_echec"] for e in etats] == [None, "UNDELIV", "EXPIRED", None]
    assert [h["etat"] for h in etats[0]["historique"]] == ["RECU", "SOUMIS", "LIVRE"]
    # Les issues finales partent en webhook, marquées TEST ; pas le sans accusé.
    espaces("worker")
    annonces = EvenementSortant.objects.filter(nom__startswith="client.message.")
    assert sorted(a.nom for a in annonces) == [
        "client.message.echec",
        "client.message.echec",
        "client.message.livre",
    ]
    assert {a.donnees["environnement"] for a in annonces} == {"TEST"}
    assert {a.client_id for a in annonces} == {compte.id}


def test_les_controles_sont_ceux_de_la_production(client, cles):
    _, essai, _ = cles
    corps = {
        "messages": [
            {"numero": "abc", "texte": "Bonjour"},
            {"numero": "99000001", "texte": "Bonjour"},
            {"numero": "70000001", "texte": " "},
            {"numero": "70000001", "texte": "Bonjour", "expediteur": "INCONNU"},
        ]
    }
    reponse = _poster(client, essai, corps)
    assert reponse.status_code == 422
    codes = [m["erreur"]["code"] for m in reponse.json()["messages"]]
    assert codes == ["NUMERO_INVALIDE", "RESEAU_INCONNU", "TEXTE_VIDE", "EXPEDITEUR_NON_AUTORISE"]


def test_une_cle_d_idempotence_d_essai_ne_sert_pas_en_production(client, cles, espaces):
    _, essai, reelle = cles
    corps = _lot(1)
    premiere = _poster(client, essai, corps, cle="commande-42")
    rejeu = _poster(client, essai, corps, cle="commande-42")
    assert rejeu.json() == premiere.json() and rejeu["Idempotent-Rejoue"] == "true"
    # La même clé en production envoie vraiment, sans rejouer la réponse d'essai.
    en_prod = _poster(client, reelle, corps, cle="commande-42")
    assert en_prod.status_code == 202 and "Idempotent-Rejoue" not in en_prod
    espaces("worker")
    assert Message.objects.count() == 1 and MessageEssai.objects.count() == 1


def test_les_essais_expires_sont_effaces(client, cles, espaces):
    _poster(client, cles[1], _lot(1))
    espaces("worker")
    assert essais.purger(timezone.now() + timedelta(days=29)) == 0
    assert essais.purger(timezone.now() + timedelta(days=31)) == 1
