"""Rapprochement quotidien du registre (T-80).

Sous le rôle du worker, comme l'ordonnanceur qui le lance. Un écart se crée en
touchant le cache directement : c'est la seule table du module qui se modifie.
"""

import logging
import time
import uuid

import pytest
from django.db.models import F

from apps.comptes.models import Client
from apps.comptes.services import PLAFONDS_PAR_PALIER, creer_client
from apps.core.models import EvenementSortant
from apps.credits import rapprochement
from apps.credits import services as credits
from apps.credits.models import CompteCredit, Ecriture

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def clients(en_tant_que_worker):
    """Deux clients aux comptes vivants : achats, réservations, règlements."""
    a = creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)
    b = creer_client(raison_sociale="Kossi Transport", forme=Client.Forme.SOCIETE)
    credits.acheter(a.id, 10_000, reference="OM-1")
    credits.acheter(b.id, 5_000, reference="OM-2")
    reservation = credits.reserver(a.id, 3_000, campagne_id=uuid.uuid7())
    credits.regler(reservation.id, 1_200)
    credits.reserver(b.id, 700, campagne_id=uuid.uuid7())
    return a, b


def _alertes():
    return list(EvenementSortant.objects.filter(nom=rapprochement.EVENEMENT).order_by("id"))


def test_des_comptes_justes_ne_levent_aucune_alerte(clients):
    assert rapprochement.rapprocher() == []
    assert _alertes() == []


def test_un_franc_d_ecart_sur_le_solde_leve_l_alerte(clients, caplog):
    a, _ = clients
    CompteCredit.objects.filter(client_id=a.id).update(solde_acquis=F("solde_acquis") + 1)

    with caplog.at_level(logging.ERROR, logger=rapprochement.__name__):
        ecarts = rapprochement.rapprocher()

    assert len(ecarts) == 1
    ecart = ecarts[0]
    assert (ecart.client_id, ecart.ecart_acquis, ecart.ecart_reserve) == (a.id, 1, 0)
    assert "Faso Riz SA" in caplog.text and "écart +1 FCFA" in caplog.text
    (alerte,) = _alertes()
    assert alerte.objet_id == a.id
    assert alerte.donnees["client"] == "Faso Riz SA"
    assert alerte.donnees["ecart_acquis"] == 1
    assert alerte.donnees["acquis_registre"] == 10_000 - 1_200


def test_un_ecart_sur_le_montant_reserve_leve_aussi_l_alerte(clients):
    _, b = clients
    CompteCredit.objects.filter(client_id=b.id).update(montant_reserve=F("montant_reserve") - 1)

    (ecart,) = rapprochement.rapprocher()

    assert (ecart.client_id, ecart.ecart_acquis, ecart.ecart_reserve) == (b.id, 0, -1)
    assert "Kossi Transport" in str(ecart) and "écart -1 FCFA" in str(ecart)
    assert _alertes()[0].donnees["reserve_recalcule"] == 700


def test_un_cache_absent_pour_un_client_qui_a_des_ecritures_est_un_ecart(clients):
    a, _ = clients
    CompteCredit.objects.filter(client_id=a.id).delete()

    (ecart,) = rapprochement.rapprocher()

    assert (ecart.acquis_cache, ecart.acquis_registre) == (0, 10_000 - 1_200)
    assert ecart.reserve_recalcule == 3_000 - 1_200


def test_mille_comptes_en_moins_d_une_minute(en_tant_que_worker):
    """Mille clients, deux écritures chacun ; un seul écart parmi eux."""
    plafonds = PLAFONDS_PAR_PALIER[Client.Palier.DECOUVERTE]
    nombre = 1_000
    lot = Client.objects.bulk_create(
        Client(
            raison_sociale=f"Client {n:04d}",
            forme=Client.Forme.SOCIETE,
            plafond_horaire=plafonds["horaire"],
            plafond_journalier=plafonds["journalier"],
        )
        for n in range(nombre)
    )
    Ecriture.objects.bulk_create(
        Ecriture(client_id=c.id, type=type_, montant=montant, solde_apres=solde, reference="OM-x")
        for c in lot
        for type_, montant, solde in (
            (Ecriture.Type.ACHAT, 1_000, 1_000),
            (Ecriture.Type.CONSOMMATION, -250, 750),
        )
    )
    CompteCredit.objects.bulk_create(CompteCredit(client_id=c.id, solde_acquis=750) for c in lot)
    fautif = lot[nombre // 2]
    CompteCredit.objects.filter(client_id=fautif.id).update(solde_acquis=751)

    debut = time.monotonic()
    ecarts = rapprochement.rapprocher()
    duree = time.monotonic() - debut

    assert [(e.client_id, e.ecart_acquis) for e in ecarts] == [(fautif.id, 1)]
    assert duree < 60, f"rapprochement de {nombre} comptes en {duree:.1f} s"
