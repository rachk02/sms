"""Registre de crédits, écritures complètes (T-77).

Les quatre types d'écriture, la trace de toute écriture manuelle, et le solde
recalculé depuis le registre qui égale le cache après mille opérations mêlées.
"""

import random
import uuid

import pytest
from django.db import IntegrityError, ProgrammingError, transaction

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.cloisonnement import poser_client
from apps.credits import services
from apps.credits.models import Ecriture, EcritureSeuleError, Reservation

pytestmark = pytest.mark.django_db


@pytest.fixture
def client():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    poser_client(client.id)
    return client


@pytest.fixture
def agent():
    return creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x-1234-abcd", nom="A")


def test_un_achat_renvoie_toujours_a_sa_transaction(client):
    for reference in ("", "   "):
        with pytest.raises(services.ReferenceObligatoire):
            services.acheter(client.id, 1_000, reference=reference)
    with pytest.raises(IntegrityError), transaction.atomic():
        Ecriture(client_id=client.id, type="ACHAT", montant=1_000, solde_apres=1_000).save()
    assert not Ecriture.objects.exists()


def test_un_paiement_manuel_porte_son_agent(client, agent):
    ecriture = services.acheter(client.id, 5_000, reference="VIR-2026-001", auteur=agent)
    assert (ecriture.type, ecriture.auteur, ecriture.reference) == (
        Ecriture.Type.ACHAT,
        agent,
        "VIR-2026-001",
    )


def test_un_recredit_manuel_exige_un_motif(client, agent):
    for motif in ("", "  "):
        with pytest.raises(services.MotifObligatoire):
            services.recrediter(client.id, 300, motif=motif, auteur=agent, reference="INC-1")
    ecriture = services.recrediter(client.id, 300, motif="Geste commercial", auteur=agent)
    assert (ecriture.auteur, ecriture.motif) == (agent, "Geste commercial")


def test_un_recredit_automatique_renvoie_a_son_message(client):
    with pytest.raises(services.ReferenceObligatoire):
        services.recrediter(client.id, 25, motif="Panne de liaison")
    ecriture = services.recrediter(client.id, 25, reference=f"message:{uuid.uuid7()}")
    assert ecriture.auteur is None and ecriture.reference.startswith("message:")


def test_la_base_refuse_un_recredit_sans_trace(client, agent):
    sans_trace = [
        {},  # ni référence, ni agent
        {"motif": "Panne"},  # motif sans agent
        {"auteur": agent},  # agent sans motif
    ]
    for champs in sans_trace:
        with pytest.raises(IntegrityError), transaction.atomic():
            Ecriture(
                client_id=client.id, type="RECREDIT", montant=100, solde_apres=100, **champs
            ).save()
    assert not Ecriture.objects.exists()


def test_une_insertion_qui_mettrait_a_jour_en_conflit_est_refusee(client):
    ecriture = services.acheter(client.id, 1_000, reference="OM-1")
    copie = Ecriture(
        id=ecriture.id,
        client_id=client.id,
        type="ACHAT",
        montant=999_999,
        solde_apres=999_999,
        reference="OM-1",
    )
    with pytest.raises(EcritureSeuleError):
        Ecriture.objects.bulk_create(
            [copie], update_conflicts=True, unique_fields=["id"], update_fields=["montant"]
        )
    # Et sans le garde du modèle, le déclencheur de la base refuse aussi.
    with pytest.raises(ProgrammingError, match="écriture seule"), transaction.atomic():
        Ecriture.objects.none().model._base_manager.bulk_create(
            [copie], update_conflicts=True, unique_fields=["id"], update_fields=["montant"]
        )
    assert Ecriture.objects.get(id=ecriture.id).montant == 1_000


def test_mille_operations_melees_laissent_le_cache_egal_au_registre(client, agent):
    hasard = random.Random(77)  # graine fixe : un échec se rejoue à l'identique
    actives: list = []
    comptes = {t: 0 for t in Ecriture.Type.values}
    services.acheter(client.id, 50_000, reference="OM-0")
    comptes[Ecriture.Type.ACHAT] += 1

    for rang in range(1_000):
        solde = services.solde(client.id)
        choix = hasard.choice(["achat", "recredit", "ajustement", "reserver", "regler", "liberer"])
        if choix == "achat":
            services.acheter(client.id, hasard.randint(1, 20_000), reference=f"OM-{rang}")
            comptes[Ecriture.Type.ACHAT] += 1
        elif choix == "recredit":
            if hasard.random() < 0.5:
                services.recrediter(client.id, hasard.randint(1, 500), reference=f"message:{rang}")
            else:
                services.recrediter(
                    client.id, hasard.randint(1, 500), motif="Geste commercial", auteur=agent
                )
            comptes[Ecriture.Type.RECREDIT] += 1
        elif choix == "ajustement":
            montant = hasard.randint(-min(solde.disponible, 1_000), 1_000)
            if montant:
                services.ajuster(client.id, montant, motif="Correction", auteur=agent)
                comptes[Ecriture.Type.AJUSTEMENT] += 1
        elif choix == "reserver" and solde.disponible > 0:
            montant = hasard.randint(1, min(solde.disponible, 5_000))
            if hasard.random() < 0.5:
                reservation = services.reserver(client.id, montant, campagne_id=uuid.uuid7())
            else:
                reservation = services.reserver(client.id, montant, message_id=uuid.uuid7())
            actives.append(reservation.id)
        elif choix == "regler" and actives:
            reservation = Reservation.objects.get(id=hasard.choice(actives))
            montant = hasard.randint(1, reservation.montant_restant)
            services.regler(reservation.id, montant, reference=f"lot-{rang}")
            comptes[Ecriture.Type.CONSOMMATION] += 1
            if montant == reservation.montant_restant:
                actives.remove(reservation.id)
        elif choix == "liberer" and actives:
            reservation_id = hasard.choice(actives)
            services.liberer(reservation_id)
            actives.remove(reservation_id)

    assert all(comptes.values()), comptes  # les quatre types ont bien été écrits
    cache = services.solde(client.id)
    assert cache == services.solde_recalcule(client.id)
    assert cache.disponible >= 0
    assert cache.reserve == sum(
        Reservation.objects.filter(id__in=actives).values_list("montant_restant", flat=True)
    )

    # Chaque ligne porte le solde acquis cumulé jusqu'à elle.
    cumul = 0
    for montant, solde_apres in (
        Ecriture.objects.filter(client_id=client.id)
        .order_by("cree_le", "id")
        .values_list("montant", "solde_apres")
    ):
        cumul += montant
        assert solde_apres == cumul
    assert cumul == cache.acquis
