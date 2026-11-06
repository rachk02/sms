"""Point d'entrée public des rappels de paiement (T-84).

Liste blanche, limite de débit, signature : un rappel qui échoue à l'un des
contrôles reçoit la même réponse nue que tous les autres refus, et la raison
va au journal applicatif. Un rappel accepté est déposé en file sans qu'aucune
table de paiement soit lue.
"""

import json
import logging
import time

import pytest
from django.db import connection
from django.test import override_settings
from django.test.utils import CaptureQueriesContext

from apps.core.models import EvenementSortant
from apps.core.redis import client_redis
from apps.paiements import rappels
from apps.paiements.fournisseurs import ENTETE_SIGNATURE, signer

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

SECRET = "secret-de-test"
ADRESSE = "/rappels/paiements/simule"
FOURNISSEUR_IP = "10.0.0.5"
LIMITE = 5


@pytest.fixture(autouse=True)
def reglages():
    with override_settings(
        PAIEMENTS_SIMULATEUR=True,
        PAIEMENTS_SIMULE_SECRET=SECRET,
        PAIEMENTS_PLAGES={"simule": ["10.0.0.0/24"]},
        PAIEMENTS_RAPPELS_PAR_MINUTE=LIMITE,
        PROXIES_DE_CONFIANCE=[],
    ):
        yield


@pytest.fixture(autouse=True)
def compteurs():
    def vider():
        redis = client_redis()
        for cle in redis.scan_iter("sms:paiements:rappels:*"):
            redis.delete(cle)

    vider()
    yield
    vider()


def _corps(reference="SIM-0001") -> bytes:
    return json.dumps({"reference": reference, "statut": "CONFIRME"}).encode()


def _envoyer(client, corps=None, *, adresse=ADRESSE, ip=FOURNISSEUR_IP, signature=True, **meta):
    corps = _corps() if corps is None else corps
    entetes = {"REMOTE_ADDR": ip, **meta}
    if signature is True:
        entetes[f"HTTP_{ENTETE_SIGNATURE.upper().replace('-', '_')}"] = signer(SECRET, corps)
    elif signature:
        entetes[f"HTTP_{ENTETE_SIGNATURE.upper().replace('-', '_')}"] = signature
    return client.post(adresse, data=corps, content_type="application/json", **entetes)


def _deposes():
    return list(EvenementSortant.objects.using("worker").filter(nom__endswith="paiements.rappels"))


def _empreinte(reponse):
    """Statut, corps et en-têtes, hors l'identifiant propre à chaque requête."""
    entetes = sorted((k, v) for k, v in reponse.headers.items() if k != "X-Request-ID")
    return reponse.status_code, reponse.content, tuple(entetes)


def test_un_rappel_signe_venu_du_fournisseur_est_depose_en_file(client):
    reponse = _envoyer(client)

    assert (reponse.status_code, reponse.content) == (200, b"OK")
    (depose,) = _deposes()
    assert depose.donnees["fournisseur"] == "SIMULE"
    assert depose.donnees["reference"] == "SIM-0001"
    assert depose.donnees["source"] == FOURNISSEUR_IP


@pytest.mark.parametrize(
    "envoi, motif",
    [
        ({"signature": False}, "signature absente"),
        ({"signature": "t=1,v1=00"}, "signature absente, fausse ou périmée"),
        ({"ip": "192.0.2.10"}, "hors liste blanche"),
        ({"adresse": "/rappels/paiements/orange"}, "fournisseur inconnu"),
    ],
    ids=["non signe", "signature fausse", "hors liste blanche", "fournisseur inconnu"],
)
def test_un_rappel_refuse_est_journalise_et_n_est_pas_depose(client, caplog, envoi, motif):
    with caplog.at_level(logging.WARNING, logger=rappels.__name__):
        reponse = _envoyer(client, **envoi)

    assert (reponse.status_code, reponse.content) == (403, b"")
    assert motif in caplog.text
    assert _deposes() == []


def test_une_signature_perimee_est_un_rejeu(client):
    corps = _corps()
    ancienne = signer(SECRET, corps, int(time.time()) - 400)
    assert _envoyer(client, corps, signature=ancienne).status_code == 403
    assert _deposes() == []


def test_une_signature_d_un_autre_corps_est_refusee(client):
    signature = signer(SECRET, _corps("SIM-0001"))
    assert _envoyer(client, _corps("SIM-9999"), signature=signature).status_code == 403


def test_tous_les_refus_se_ressemblent(client):
    """Ni le fournisseur, ni le contrôle qui a arrêté le rappel ne se devinent."""
    refus = [
        _envoyer(client, signature=False),
        _envoyer(client, ip="192.0.2.10"),
        _envoyer(client, adresse="/rappels/paiements/orange"),
        _envoyer(client, adresse="/rappels/paiements/moov", ip="192.0.2.10"),
        _envoyer(client, _corps()[:-3]),  # signé mais illisible
        client.get(ADRESSE, REMOTE_ADDR=FOURNISSEUR_IP),
    ]
    assert len({_empreinte(r) for r in refus}) == 1


def test_la_reponse_ne_dit_rien_de_l_existence_d_une_transaction(client):
    """Aucune lecture d'une table de paiement : la même réponse pour toute
    référence, connue ou non."""
    with CaptureQueriesContext(connection) as requetes:
        premiere = _envoyer(client, _corps("SIM-EXISTE-PEUT-ETRE"))
    seconde = _envoyer(client, _corps("SIM-INVENTEE"))

    assert _empreinte(premiere) == _empreinte(seconde)
    assert not [q["sql"] for q in requetes.captured_queries if "paiements_" in q["sql"]]


def test_la_limite_de_debit_par_source(client, caplog):
    for _ in range(LIMITE):
        assert _envoyer(client).status_code == 200
    with caplog.at_level(logging.WARNING, logger=rappels.__name__):
        assert _envoyer(client).status_code == 403
    assert "limite de débit" in caplog.text
    assert len(_deposes()) == LIMITE
    # Une autre source du fournisseur n'est pas touchée.
    assert _envoyer(client, ip="10.0.0.6").status_code == 200


def test_le_meme_rappel_recu_trois_fois_arrive_trois_fois_au_worker(client):
    """L'idempotence est l'affaire du worker (T-85), pas de la boîte d'envoi."""
    corps = _corps("SIM-0042")
    for _ in range(3):
        assert _envoyer(client, corps).status_code == 200
    assert [d.donnees["reference"] for d in _deposes()] == ["SIM-0042"] * 3


def test_un_x_forwarded_for_ne_vaut_que_derriere_un_frontal_de_confiance(client):
    forge = {"HTTP_X_FORWARDED_FOR": FOURNISSEUR_IP}
    assert _envoyer(client, ip="192.0.2.10", **forge).status_code == 403

    with override_settings(PROXIES_DE_CONFIANCE=["172.16.0.1/32"]):
        derriere = {"HTTP_X_FORWARDED_FOR": f"203.0.113.9, {FOURNISSEUR_IP}"}
        assert _envoyer(client, ip="172.16.0.1", **derriere).status_code == 200
        # Seule la dernière adresse, ajoutée par le frontal, compte.
        trompeur = {"HTTP_X_FORWARDED_FOR": f"{FOURNISSEUR_IP}, 203.0.113.9"}
        assert _envoyer(client, ip="172.16.0.1", **trompeur).status_code == 403


def test_sans_secret_ni_plage_tout_est_refuse(client):
    with override_settings(PAIEMENTS_SIMULE_SECRET=""):
        assert _envoyer(client).status_code == 403
    with override_settings(PAIEMENTS_PLAGES={"simule": []}):
        assert _envoyer(client).status_code == 403
    assert _deposes() == []


def test_le_simulateur_coupe_n_existe_plus(client):
    with override_settings(PAIEMENTS_SIMULATEUR=False):
        assert _envoyer(client).status_code == 403
