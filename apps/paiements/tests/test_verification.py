"""Vérification auprès du fournisseur avant tout crédit (T-85).

Sous le rôle du worker, comme le worker paiements. Le fournisseur simulé
répond ce que le test lui a fait savoir (`fournisseurs.simuler`).
"""

import json
import logging
import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.models import EvenementSortant
from apps.core.redis import client_redis
from apps.credits import services as credits
from apps.credits.models import Ecriture
from apps.paiements import fournisseurs, services
from apps.paiements.models import Transaction
from workers.paiements import _verifier

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

Etat = Transaction.Etat
CREDITS, MONTANT = 10_000, 11_800


@pytest.fixture(autouse=True)
def simulateur():
    def vider():
        redis = client_redis()
        for cle in redis.scan_iter("sms:paiements:simule:*"):
            redis.delete(cle)

    vider()
    with override_settings(PAIEMENTS_SIMULATEUR=True):
        yield
    vider()


@pytest.fixture
def client(en_tant_que_worker):
    return creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)


def _transaction(client, reference="SIM-0001", etat=Etat.EN_ATTENTE, **champs):
    return Transaction.objects.create(
        client=client,
        fournisseur=Transaction.Fournisseur.SIMULE,
        reference_transaction=reference,
        montant=MONTANT,
        credits=CREDITS,
        etat=etat,
        expire_le=timezone.now() + timedelta(minutes=15),
        **champs,
    )


def _achats(client):
    return list(Ecriture.objects.filter(client_id=client.id, type=Ecriture.Type.ACHAT))


def _evenements(nom):
    return list(EvenementSortant.objects.filter(nom=nom))


def test_un_paiement_confirme_credite_les_credits_hors_taxes(client):
    transaction_ = _transaction(client)
    fournisseurs.simuler("SIM-0001", montant=MONTANT, etat="CONFIRME")

    services.verifier("SIMULE", "SIM-0001")

    transaction_.refresh_from_db()
    assert transaction_.etat == Etat.CONFIRME and transaction_.confirme_le
    (achat,) = _achats(client)
    assert (achat.montant, achat.reference) == (CREDITS, f"paiement:{transaction_.id}")
    assert (
        credits.solde(client.id) == credits.solde_recalcule(client.id) == credits.Solde(CREDITS, 0)
    )
    (confirme,) = _evenements("paiements.transaction.confirmee")
    assert confirme.objet_id == transaction_.id


def test_un_rappel_forge_avec_une_reference_inexistante_ne_credite_rien(client, caplog):
    _transaction(client, reference="SIM-0001")
    # Le faussaire a même un paiement réel à son nom, chez le fournisseur, sous une
    # référence que la plateforme n'a jamais émise.
    fournisseurs.simuler("SIM-FORGEE", montant=MONTANT, etat="CONFIRME")

    with caplog.at_level(logging.WARNING, logger=services.__name__):
        assert services.verifier("SIMULE", "SIM-FORGEE") is None

    assert "référence inconnue" in caplog.text
    assert _achats(client) == []
    assert not Transaction.objects.filter(reference_transaction="SIM-FORGEE").exists()


def test_un_rappel_pour_une_reference_connue_mais_impayee_ne_credite_rien(client):
    transaction_ = _transaction(client)
    for statut in (None, "EN_ATTENTE"):
        if statut:
            fournisseurs.simuler("SIM-0001", montant=MONTANT, etat=statut)
        services.verifier("SIMULE", "SIM-0001")
        transaction_.refresh_from_db()
        assert transaction_.etat == Etat.EN_ATTENTE
    assert _achats(client) == []


def test_le_meme_rappel_recu_trois_fois_ne_credite_qu_une_fois(client):
    _transaction(client)
    fournisseurs.simuler("SIM-0001", montant=MONTANT, etat="CONFIRME")
    corps = json.dumps({"donnees": {"fournisseur": "SIMULE", "reference": "SIM-0001"}}).encode()

    for _ in range(3):
        _verifier(corps)  # tel que le worker le reçoit de la file

    assert [a.montant for a in _achats(client)] == [CREDITS]
    assert len(_evenements("paiements.transaction.confirmee")) == 1


@pytest.mark.parametrize("essai", range(3))
def test_trois_rappels_simultanes_ne_creditent_qu_une_fois(client, essai):
    """Trois fils, trois connexions, trois transactions réelles."""
    _transaction(client)
    fournisseurs.simuler("SIM-0001", montant=MONTANT, etat="CONFIRME")
    depart, erreurs = threading.Barrier(3), []

    def rappel():
        try:
            depart.wait(5)
            services.verifier("SIMULE", "SIM-0001")
        except Exception as erreur:  # noqa: BLE001
            erreurs.append(erreur)
        finally:
            connection.close()

    fils = [threading.Thread(target=rappel) for _ in range(3)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(30)

    assert not erreurs
    assert [a.montant for a in _achats(client)] == [CREDITS]
    assert credits.solde(client.id) == credits.solde_recalcule(client.id)


def test_un_rappel_tardif_confirme_par_l_api_credite_quand_meme(client):
    transaction_ = _transaction(client, etat=Etat.EXPIRE)
    fournisseurs.simuler("SIM-0001", montant=MONTANT, etat="CONFIRME")

    services.verifier("SIMULE", "SIM-0001")

    transaction_.refresh_from_db()
    assert transaction_.etat == Etat.CONFIRME
    assert [a.montant for a in _achats(client)] == [CREDITS]


def test_un_montant_confirme_different_ne_credite_rien_et_alerte(client, caplog):
    transaction_ = _transaction(client)
    fournisseurs.simuler("SIM-0001", montant=MONTANT - 100, etat="CONFIRME")

    with caplog.at_level(logging.ERROR, logger=services.__name__):
        services.verifier("SIMULE", "SIM-0001")

    transaction_.refresh_from_db()
    assert transaction_.etat == Etat.EN_ATTENTE
    assert _achats(client) == []
    assert "montant confirmé différent" in caplog.text
    (anomalie,) = _evenements("paiements.transaction.anomalie")
    assert (anomalie.donnees["attendu"], anomalie.donnees["confirme"]) == (MONTANT, MONTANT - 100)


def test_un_paiement_refuse_echoue_et_ne_se_reprend_pas(client, caplog):
    transaction_ = _transaction(client)
    fournisseurs.simuler("SIM-0001", montant=MONTANT, etat="ECHOUE")
    services.verifier("SIMULE", "SIM-0001")
    transaction_.refresh_from_db()
    assert transaction_.etat == Etat.ECHOUE

    # Un fournisseur qui se contredit ensuite ne crédite pas en silence : il alerte.
    fournisseurs.simuler("SIM-0001", montant=MONTANT, etat="CONFIRME")
    with caplog.at_level(logging.ERROR, logger=services.__name__):
        services.verifier("SIMULE", "SIM-0001")
    transaction_.refresh_from_db()
    assert transaction_.etat == Etat.ECHOUE
    assert _achats(client) == []
    assert "alors que ECHOUE" in caplog.text


def test_la_base_refuse_deux_transactions_sous_la_meme_reference(client):
    from django.db import IntegrityError, transaction

    _transaction(client)
    with pytest.raises(IntegrityError), transaction.atomic():
        _transaction(client)
