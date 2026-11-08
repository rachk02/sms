"""Routes de consultation (T-109).

Le journal impose une plage de dates comme l'interface. La pagination par
curseur est stable même si des messages sont insérés pendant le parcours. Le
solde distingue acquis, réservé et disponible.
"""

import json
import uuid

import pytest
from django.utils import timezone

from apps.backoffice.tests.test_clients import _preparer
from apps.comptes.services import creer_cle
from apps.expediteurs.models import Expediteur, ExpediteurReseau
from apps.telecom.models import Prefixe

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def compte(espaces, settings):
    settings.SMS_PRIX_SEGMENT_PROVISOIRE = 20
    settings.SMS_NUMERO_COURT = "36000"
    espaces("worker")
    client = _preparer()
    _, jeton = creer_cle(client=client, nom="Site", auteur=None)
    espaces("client")
    yield client, {"HTTP_AUTHORIZATION": f"Bearer {jeton}"}
    espaces("worker")
    Prefixe.objects.filter(pays="BF").delete()


def _envoyer(client, entetes, nombre, debut=0):
    corps = {
        "messages": [
            {"numero": f"70{rang:06d}", "texte": "Bonjour"} for rang in range(debut, debut + nombre)
        ]
    }
    reponse = client.post(
        "/v1/messages", json.dumps(corps), content_type="application/json", **entetes
    )
    assert reponse.status_code == 202
    return [sort["id"] for sort in reponse.json()["messages"]]


def test_le_statut_d_un_message_et_son_historique(client, compte):
    _, entetes = compte
    (identifiant,) = _envoyer(client, entetes, 1)
    corps = client.get(f"/v1/messages/{identifiant}", **entetes).json()
    assert corps["id"] == identifiant and corps["etat"] == "RECU"
    assert corps["segments"] == 1 and corps["cout"] == 20
    assert [etape["etat"] for etape in corps["historique"]] == ["RECU"]
    # Inconnu, ou pas un UUID version 7 : 404, sans rien dire de plus.
    for autre in (uuid.uuid7(), uuid.uuid4()):
        reponse = client.get(f"/v1/messages/{autre}", **entetes)
        assert reponse.status_code == 404
        assert reponse.json()["erreur"]["code"] == "MESSAGE_INCONNU"


def test_le_message_d_un_autre_compte_est_inconnu(client, compte, espaces):
    _, entetes = compte
    (identifiant,) = _envoyer(client, entetes, 1)
    espaces("worker")
    from apps.comptes.models import Client
    from apps.comptes.services import creer_client

    autre = creer_client(raison_sociale="Autre SARL", forme=Client.Forme.SOCIETE)
    _, jeton = creer_cle(client=autre, nom="ERP", auteur=None)
    espaces("client")
    reponse = client.get(f"/v1/messages/{identifiant}", HTTP_AUTHORIZATION=f"Bearer {jeton}")
    assert reponse.status_code == 404


def test_le_journal_impose_une_plage_de_dates(client, compte):
    _, entetes = compte
    _envoyer(client, entetes, 2)
    aujourd_hui = timezone.localdate().isoformat()
    # Une seule borne, une plage à l'envers ou trop longue : refusées.
    for parametres in (
        {"debut": aujourd_hui},
        {"debut": aujourd_hui, "fin": "2020-01-01"},
        {"debut": "2026-01-01", "fin": "2026-06-30"},
        {"limite": "x"},
        {"limite": "500"},
    ):
        reponse = client.get("/v1/messages", parametres, **entetes)
        assert reponse.status_code == 400, parametres
        assert reponse.json()["erreur"]["code"] == "RECHERCHE_REFUSEE"
    # Sans aucune date : la dernière semaine, comme à l'écran.
    corps = client.get("/v1/messages", **entetes).json()
    assert len(corps["messages"]) == 2 and corps["suivant"] is None


def test_la_pagination_est_stable_malgre_les_insertions(client, compte):
    _, entetes = compte
    premiers = _envoyer(client, entetes, 5)
    aujourd_hui = timezone.localdate().isoformat()
    parametres = {"debut": aujourd_hui, "fin": aujourd_hui, "limite": 2}
    page = client.get("/v1/messages", parametres, **entetes).json()
    vus = [m["id"] for m in page["messages"]]
    # Des messages arrivent pendant le parcours : ils sont plus récents que le
    # curseur, la suite du parcours ne les montre pas et ne saute rien.
    nouveaux = _envoyer(client, entetes, 3, debut=100)
    while page["suivant"]:
        page = client.get(
            "/v1/messages", {**parametres, "apres": page["suivant"]}, **entetes
        ).json()
        vus += [m["id"] for m in page["messages"]]
    assert sorted(vus) == sorted(premiers)
    assert len(vus) == len(set(vus)) == 5
    assert not set(vus) & set(nouveaux)
    # Un curseur abîmé est refusé, pas ignoré.
    reponse = client.get("/v1/messages", {**parametres, "apres": "abime"}, **entetes)
    assert reponse.status_code == 400


def test_le_solde_distingue_acquis_reserve_et_disponible(client, compte):
    _, entetes = compte
    _envoyer(client, entetes, 3)
    corps = client.get("/v1/solde", **entetes).json()
    assert corps == {
        "acquis": 1_000_000,
        "reserve": 60,
        "disponible": 1_000_000 - 60,
        "devise": "XOF",
    }


def test_les_expediteurs_et_leur_etat_par_reseau(client, compte, espaces):
    compte_client, entetes = compte
    espaces("worker")
    expediteur = Expediteur.objects.create(
        client=compte_client, libelle="FASORIZ", etat=Expediteur.Etat.ACTIF
    )
    ExpediteurReseau.objects.create(
        client=compte_client,
        expediteur=expediteur,
        operateur="ORANGE",
        etat=ExpediteurReseau.Etat.APPROUVE,
    )
    espaces("client")
    corps = client.get("/v1/expediteurs", **entetes).json()
    (ligne,) = corps["expediteurs"]
    assert ligne["libelle"] == "FASORIZ" and ligne["etat"] == "ACTIF"
    assert ligne["reseaux"]["ORANGE"]["etat"] == "APPROUVE"
    assert ligne["reseaux"]["MOOV"] is None
