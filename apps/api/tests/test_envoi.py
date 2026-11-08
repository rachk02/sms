"""Route d'envoi (T-108).

Un rejeu avec la même clé d'idempotence rend la même réponse sans envoyer une
seconde fois. Un lot de mille messages est accepté en moins de deux secondes.
Un message invalide dans un lot n'annule pas les autres, la réponse détaille
les rejets.
"""

import json
import time
import uuid

import pytest
from django.db import connections
from django.test.utils import CaptureQueriesContext

from apps.backoffice.tests.test_clients import _preparer
from apps.comptes.models import Client
from apps.comptes.services import creer_cle
from apps.core.models import EvenementSortant
from apps.credits import services as credits
from apps.credits.models import Reservation
from apps.journal.models import Message
from apps.telecom.models import Prefixe

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
]

ENVOI = "/v1/messages"


@pytest.fixture
def compte(espaces, settings):
    settings.SMS_PRIX_SEGMENT_PROVISOIRE = 20
    settings.SMS_NUMERO_COURT = "36000"
    espaces("worker")
    client = _preparer()
    _, jeton = creer_cle(client=client, nom="Site", auteur=None)
    espaces("client")
    yield client, jeton
    espaces("worker")
    Prefixe.objects.filter(pays="BF").delete()


def _poster(client, jeton, corps, cle=None):
    entetes = {"HTTP_AUTHORIZATION": f"Bearer {jeton}"}
    if cle:
        entetes["HTTP_IDEMPOTENCY_KEY"] = cle
    return client.post(ENVOI, json.dumps(corps), content_type="application/json", **entetes)


def _lot(nombre, debut=0):
    return {
        "messages": [
            # Sans chiffres : un code (quatre à huit chiffres) passe par la
            # détection anti-fraude, qui a ses propres tests.
            {"numero": f"70{rang:06d}", "texte": "Votre commande est prête."}
            for rang in range(debut, debut + nombre)
        ]
    }


def test_un_envoi_est_accepte_tout_de_suite_avec_son_identifiant(client, compte):
    compte_client, jeton = compte
    reponse = _poster(client, jeton, {"numero": "70 00 00 01", "texte": "Bonjour"})
    assert reponse.status_code == 202
    corps = reponse.json()
    assert (corps["acceptes"], corps["rejetes"]) == (1, 0)
    sort = corps["messages"][0]
    assert sort["etat"] == "RECU" and sort["numero"] == "+22670000001"
    assert sort["expediteur"] == "36000" and sort["cout"] == 20
    # Écrit au journal, son coût réservé, déposé dans la file prioritaire.
    message = Message.objects.using("worker").get(id=sort["id"])
    assert Reservation.objects.using("worker").get(message_id=message.id).montant_initial == 20
    assert (
        EvenementSortant.objects.using("worker")
        .filter(cle_idempotence=f"envoi:{message.id}", nom__contains="prioritaire")
        .exists()
    )
    assert reponse["Quota-Compte-Heure-Restant"] == str(compte_client.plafond_horaire - 1)


def test_un_rejeu_rend_la_meme_reponse_sans_envoyer_une_seconde_fois(client, compte):
    _, jeton = compte
    corps = _lot(3)
    premiere = _poster(client, jeton, corps, cle="commande-42")
    seconde = _poster(client, jeton, corps, cle="commande-42")
    assert premiere.status_code == seconde.status_code == 202
    assert seconde.json() == premiere.json()
    assert seconde["Idempotent-Rejoue"] == "true"
    assert Message.objects.using("worker").count() == 3
    assert Reservation.objects.using("worker").count() == 3
    # La même clé pour un autre corps est refusée, rien n'est envoyé.
    autre = _poster(client, jeton, _lot(1, debut=50), cle="commande-42")
    assert autre.status_code == 422
    assert autre.json()["erreur"]["code"] == "IDEMPOTENCE_REUTILISEE"
    assert Message.objects.using("worker").count() == 3


def test_un_message_invalide_n_annule_pas_les_autres(client, compte):
    _, jeton = compte
    corps = {
        "messages": [
            {"numero": "70000001", "texte": "Bonjour"},
            {"numero": "abc", "texte": "Bonjour"},
            {"numero": "70000002", "texte": "   "},
            {"numero": "99000001", "texte": "Bonjour"},
            {"numero": "70000003", "texte": "Bonjour", "expediteur": "INCONNU"},
            {"numero": "70000004", "texte": "x" * 2000},
            {"numero": "70000005", "texte": "Au revoir"},
        ]
    }
    reponse = _poster(client, jeton, corps)
    assert reponse.status_code == 202
    corps = reponse.json()
    assert (corps["acceptes"], corps["rejetes"]) == (2, 5)
    codes = [sort.get("erreur", {}).get("code", "") for sort in corps["messages"]]
    assert codes == [
        "",
        "NUMERO_INVALIDE",
        "TEXTE_VIDE",
        "RESEAU_INCONNU",
        "EXPEDITEUR_NON_AUTORISE",
        "TEXTE_TROP_LONG",
        "",
    ]
    assert [sort["rang"] for sort in corps["messages"]] == list(range(7))
    # Rien d'accepté : 422, avec le détail.
    tout_faux = _poster(client, jeton, {"numero": "abc", "texte": "x"})
    assert tout_faux.status_code == 422
    assert tout_faux.json()["messages"][0]["erreur"]["code"] == "NUMERO_INVALIDE"


def test_le_plafond_puis_le_solde_refusent_la_suite_du_lot(client, compte, espaces):
    compte_client, jeton = compte
    espaces("worker")
    Client.objects.filter(id=compte_client.id).update(plafond_horaire=4)
    disponible = credits.solde(compte_client.id).disponible
    # Ne laisser que de quoi payer deux messages de 20 : le reste est retenu
    # par une campagne.
    credits.reserver(compte_client.id, disponible - 40, campagne_id=uuid.uuid7())
    espaces("client")
    corps = _poster(client, jeton, _lot(5)).json()
    codes = [sort.get("erreur", {}).get("code", "") for sort in corps["messages"]]
    assert codes == ["", "", "SOLDE_INSUFFISANT", "SOLDE_INSUFFISANT", "PLAFOND_COMPTE"]


def test_un_lot_de_mille_est_accepte_en_moins_de_deux_secondes(client, compte):
    _, jeton = compte
    # Le nombre de requêtes ne dépend pas de la taille du lot.
    with CaptureQueriesContext(connections["default"]) as petit:
        assert _poster(client, jeton, _lot(10)).status_code == 202
    debut = time.perf_counter()
    with CaptureQueriesContext(connections["default"]) as grand:
        reponse = _poster(client, jeton, _lot(1000, debut=10))
    duree = time.perf_counter() - debut
    assert reponse.status_code == 202 and reponse.json()["acceptes"] == 1000
    assert len(grand.captured_queries) == len(petit.captured_queries)
    assert duree < 2.0, duree
    assert Message.objects.using("worker").count() == 1010


def test_les_corps_mal_formes_sont_refuses(client, compte):
    _, jeton = compte
    entetes = {"HTTP_AUTHORIZATION": f"Bearer {jeton}"}
    assert (
        client.post(ENVOI, "pas du json", content_type="application/json", **entetes).json()[
            "erreur"
        ]["code"]
        == "CORPS_INVALIDE"
    )
    assert _poster(client, jeton, {"messages": "x"}).status_code == 400
    assert _poster(client, jeton, {"messages": []}).status_code == 400
    trop = _poster(client, jeton, _lot(1001))
    assert trop.status_code == 400 and trop.json()["erreur"]["code"] == "LOT_TROP_GRAND"
    assert client.get(ENVOI, **entetes).status_code == 405
