"""Réservation et règlement selon le mode du client (T-78).

Règlement à la soumission, le mode de tous : un message accepté par le réseau
consomme son coût. Règlement à la livraison, sur option : seul un message livré,
ou resté sans accusé, consomme ; un échec rend exactement sa réservation. Les
deux modes passent par la même réservation, seul le moment du règlement change.
"""

import uuid

import pytest
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import changer_mode_reglement, creer_client, creer_utilisateur
from apps.core.audit import Audit
from apps.core.cloisonnement import poser_client
from apps.core.etats import Etat
from apps.credits import services
from apps.credits.models import Ecriture, Reservation

pytestmark = pytest.mark.django_db

COUT = 45


@pytest.fixture
def agent():
    return creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x-1234-abcd", nom="A")


def _client(mode=Client.Reglement.SOUMISSION, agent=None):
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    poser_client(client.id)
    if mode != Client.Reglement.SOUMISSION:
        changer_mode_reglement(client, mode, auteur=agent, motif="Contrat grand compte")
    services.acheter(client.id, 10_000, reference="OM-1")
    return client


def _unitaire(client):
    message_id = uuid.uuid7()
    services.reserver(client.id, COUT, message_id=message_id, message_date_reception=timezone.now())
    return message_id


def _consommations(client):
    return list(
        Ecriture.objects.filter(client_id=client.id, type=Ecriture.Type.CONSOMMATION)
        .order_by("cree_le")
        .values_list("montant", flat=True)
    )


def test_la_reservation_fige_le_mode_du_client(agent):
    client = _client()
    avant = Reservation.objects.get(message_id=_unitaire(client))
    changer_mode_reglement(client, Client.Reglement.LIVRAISON, auteur=agent, motif="Avenant")
    apres = Reservation.objects.get(message_id=_unitaire(client))
    assert (avant.mode, apres.mode) == (Client.Reglement.SOUMISSION, Client.Reglement.LIVRAISON)


def test_le_changement_de_mode_est_motive_et_audite(en_tant_que_worker, agent):
    # Le journal d'audit ne se lit pas sous le rôle applicatif : sous celui des workers.
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    with pytest.raises(ValueError):
        changer_mode_reglement(client, Client.Reglement.LIVRAISON, auteur=agent, motif=" ")
    with pytest.raises(ValueError):
        changer_mode_reglement(client, "AU_HASARD", auteur=agent, motif="x")
    changer_mode_reglement(client, Client.Reglement.LIVRAISON, auteur=agent, motif="Avenant n 2")
    trace = Audit.objects.get(action="comptes.mode_reglement")
    assert (trace.valeur_avant, trace.valeur_apres, trace.motif, trace.auteur) == (
        {"mode_reglement": "SOUMISSION"},
        {"mode_reglement": "LIVRAISON"},
        "Avenant n 2",
        agent,
    )
    assert Client.objects.get(id=client.id).mode_reglement == Client.Reglement.LIVRAISON


def test_a_la_soumission_le_message_consomme_et_l_accuse_ne_change_rien():
    client = _client()
    message_id = _unitaire(client)
    services.solder_message(client_id=client.id, message_id=message_id, montant=COUT, soumis=True)
    assert _consommations(client) == [-COUT]
    # L'accusé d'échec arrive ensuite, sur une réservation déjà réglée : rien.
    assert (
        services.solder_final(
            client_id=client.id, message_id=message_id, montant=COUT, etat=Etat.NON_LIVRE
        )
        is None
    )
    assert _consommations(client) == [-COUT]


@pytest.mark.parametrize("etat", [Etat.LIVRE, Etat.SANS_ACCUSE])
def test_a_la_livraison_le_message_consomme_a_son_etat_final(agent, etat):
    client = _client(Client.Reglement.LIVRAISON, agent)
    message_id = _unitaire(client)
    services.solder_message(client_id=client.id, message_id=message_id, montant=COUT, soumis=True)
    assert _consommations(client) == []  # soumis : rien n'est encore dû
    assert services.solde(client.id).reserve == COUT

    services.solder_final(client_id=client.id, message_id=message_id, montant=COUT, etat=etat)
    services.solder_final(client_id=client.id, message_id=message_id, montant=COUT, etat=etat)
    assert _consommations(client) == [-COUT]  # une fois, malgré le rejeu
    assert services.solde(client.id) == services.Solde(10_000 - COUT, 0)
    assert services.solde(client.id) == services.solde_recalcule(client.id)


@pytest.mark.parametrize("etat", [Etat.NON_LIVRE, Etat.EXPIRE, Etat.REJETE])
def test_a_la_livraison_un_echec_rend_exactement_sa_reservation(agent, etat):
    client = _client(Client.Reglement.LIVRAISON, agent)
    message_id = _unitaire(client)
    assert services.solde(client.id).disponible == 10_000 - COUT
    services.solder_message(client_id=client.id, message_id=message_id, montant=COUT, soumis=True)

    services.solder_final(client_id=client.id, message_id=message_id, montant=COUT, etat=etat)
    services.solder_final(client_id=client.id, message_id=message_id, montant=COUT, etat=etat)
    assert _consommations(client) == []
    assert Reservation.objects.get(message_id=message_id).etat == Reservation.Etat.LIBEREE
    assert services.solde(client.id) == services.Solde(10_000, 0)


def test_un_refus_avant_le_reseau_ne_coute_rien_dans_les_deux_modes(agent):
    for mode in Client.Reglement.values:
        client = _client(mode, agent)
        message_id = _unitaire(client)
        services.solder_message(
            client_id=client.id, message_id=message_id, montant=COUT, soumis=False
        )
        # Rejoué : la réservation déjà libérée n'est pas une anomalie.
        services.solder_message(
            client_id=client.id, message_id=message_id, montant=COUT, soumis=False
        )
        assert _consommations(client) == []
        assert services.solde(client.id) == services.Solde(10_000, 0)


def test_une_campagne_a_la_livraison_ne_consomme_que_ses_livres(agent):
    client = _client(Client.Reglement.LIVRAISON, agent)
    campagne_id = uuid.uuid7()
    reservation = services.reserver(client.id, 4 * COUT, campagne_id=campagne_id)
    etats = [Etat.LIVRE, Etat.NON_LIVRE, Etat.SANS_ACCUSE, Etat.EXPIRE]
    messages = [uuid.uuid7() for _ in etats]
    for message_id in messages:
        services.solder_message(
            client_id=client.id,
            message_id=message_id,
            campagne_id=campagne_id,
            montant=COUT,
            soumis=True,
        )
    assert _consommations(client) == []
    for message_id, etat in zip(messages, etats, strict=True):
        services.solder_final(
            client_id=client.id,
            message_id=message_id,
            campagne_id=campagne_id,
            montant=COUT,
            etat=etat,
        )
    assert _consommations(client) == [-COUT, -COUT]
    # Les échecs ne libèrent rien en cours de route : le reliquat attend la fin.
    restant = Reservation.objects.get(id=reservation.id)
    assert (restant.etat, restant.montant_restant) == (Reservation.Etat.ACTIVE, 2 * COUT)
    assert services.liberer(reservation.id) == 2 * COUT
    assert services.solde(client.id) == services.Solde(10_000 - 2 * COUT, 0)


def test_l_ordonnanceur_regle_les_envois_unitaires_a_leur_etat_final(en_tant_que_worker, agent):
    from apps.journal.models import Evenement
    from apps.journal.services import changer_etat, enregistrer_message
    from workers.ordonnanceur import solder_envois_unitaires

    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    changer_mode_reglement(client, Client.Reglement.LIVRAISON, auteur=agent, motif="Contrat")
    services.acheter(client.id, 10_000, reference="OM-1")
    finals = {}
    for rang, etat in enumerate([Etat.LIVRE, Etat.NON_LIVRE, Etat.SOUMIS]):
        message = enregistrer_message(
            client=client,
            numero_normalise=f"+2267012345{rang}",
            operateur="ORANGE",
            expediteur_libelle="36000",
            texte="Votre code : 123456",
            cout_unitaire=COUT,
        )
        services.reserver(
            client.id,
            message.cout_total,
            message_id=message.id,
            message_date_reception=message.date_reception,
        )
        changer_etat(
            message,
            Etat.SOUMIS,
            source=Evenement.Source.WORKER,
            date_soumission=timezone.now(),
        )
        if etat != Etat.SOUMIS:
            message.etat = Etat.SOUMIS
            changer_etat(message, etat, source=Evenement.Source.ACCUSE)
        finals[etat] = message.id

    assert solder_envois_unitaires() == 2
    assert solder_envois_unitaires() == 0  # rien de plus au passage suivant
    reservations = {r.message_id: r.etat for r in Reservation.objects.all()}
    assert reservations == {
        finals[Etat.LIVRE]: Reservation.Etat.REGLEE,
        finals[Etat.NON_LIVRE]: Reservation.Etat.LIBEREE,
        finals[Etat.SOUMIS]: Reservation.Etat.ACTIVE,  # attend son accusé
    }
    assert services.solde(client.id) == services.Solde(10_000 - COUT, COUT)
