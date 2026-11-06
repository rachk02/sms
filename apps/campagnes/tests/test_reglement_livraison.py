# Les fixtures de test_pause sont importées : leurs noms reviennent en paramètres.
# ruff: noqa: F811

"""Une campagne réglée à la livraison, de la confirmation au reliquat (T-78).

La campagne se clôt quand tout est soumis, comme en règlement à la soumission ;
sa réservation attend que chaque message ait son état final, livré, en échec
ou sans accusé ; l'ordonnanceur conclut alors : consommation des seuls livrés
et sans accusé, à leur coût figé, puis libération du reliquat.
"""

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.campagnes import diffusion
from apps.campagnes.models import Campagne
from apps.campagnes.tests.test_pause import (  # noqa: F401  (fixtures)
    ACHAT,
    PRIX,
    _confirmee,
    _en_file,
    client,
    reglages,
    worker,
)
from apps.comptes.models import Client
from apps.comptes.services import changer_mode_reglement
from apps.core.etats import Etat
from apps.credits import services as credits
from apps.credits.models import Ecriture, Reservation
from apps.journal.models import Message
from apps.journal.services import appliquer_accuse, constater_sans_accuse
from apps.telecom import correlation
from apps.telecom.passerelles import Accuse

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("reglages"),
]

LIBELLES = {Etat.LIVRE: "DELIVRD", Etat.NON_LIVRE: "UNDELIV", Etat.EXPIRE: "EXPIRED"}


def _livraison(client):
    return changer_mode_reglement(
        client, Client.Reglement.LIVRAISON, auteur=None, motif="Contrat grand compte"
    )


def _diffuser(worker, campagne):
    diffusion.publier()
    for donnees in _en_file(campagne):
        worker._soumettre(donnees)
    return list(Message.objects.filter(campagne_id=campagne.id).order_by("numero_normalise"))


def _accuser(message, etat):
    return appliquer_accuse(
        Accuse(
            connecteur_id=message.connecteur_id,
            reference_smsc=message.reference_smsc,
            libelle_operateur=LIBELLES[etat],
            etat=etat,
            horodatage=timezone.now(),
        )
    )


def _consommes(campagne):
    ids = Message.objects.filter(campagne_id=campagne.id).values_list("id", flat=True)
    return sorted(
        Ecriture.objects.filter(
            type=Ecriture.Type.CONSOMMATION, reference__in=[f"message:{i}" for i in ids]
        ).values_list("reference", "montant")
    )


def _apres_la_validite():
    return timezone.now() + correlation.validite() + timedelta(minutes=1)


def test_seuls_les_livres_et_les_sans_accuse_sont_factures(client, worker):
    _livraison(client)
    campagne = _confirmee(client, 6)
    reservation = Reservation.objects.get(campagne_id=campagne.id)
    assert (reservation.mode, reservation.montant_initial) == (Client.Reglement.LIVRAISON, 6 * PRIX)

    messages = _diffuser(worker, campagne)
    assert {m.etat for m in messages} == {Etat.SOUMIS}
    assert _consommes(campagne) == []  # soumis : rien n'est encore dû

    # Tout est soumis : la campagne se clôt, sa réservation attend les accusés.
    diffusion.publier()
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.TERMINEE and campagne.cout_reel == 0
    assert Reservation.objects.get(id=reservation.id).etat == Reservation.Etat.ACTIVE

    for message, etat in zip(
        messages, [Etat.LIVRE, Etat.LIVRE, Etat.LIVRE, Etat.NON_LIVRE, Etat.EXPIRE], strict=False
    ):
        assert _accuser(message, etat)
    assert _accuser(messages[0], Etat.LIVRE) is False  # rejoué
    diffusion.publier()  # un message attend encore son accusé : rien n'est conclu
    assert Reservation.objects.get(id=reservation.id).etat == Reservation.Etat.ACTIVE

    # Le sixième reste sans accusé au terme de la validité : il est facturé.
    assert len(constater_sans_accuse(_apres_la_validite())) == 1
    diffusion.publier()
    diffusion.publier()  # un second passage ne consomme rien de plus

    factures = [m for m in messages if m.id != messages[3].id and m.id != messages[4].id]
    assert _consommes(campagne) == sorted((f"message:{m.id}", -PRIX) for m in factures)
    reservation.refresh_from_db()
    assert reservation.etat == Reservation.Etat.LIBEREE
    campagne.refresh_from_db()
    assert campagne.cout_reel == 4 * PRIX
    assert credits.solde(client.id) == credits.Solde(ACHAT - 4 * PRIX, 0)
    assert credits.solde(client.id) == credits.solde_recalcule(client.id)


def test_le_meme_envoi_a_la_soumission_facture_tout_ce_qui_est_parti(client, worker):
    campagne = _confirmee(client, 6)
    messages = _diffuser(worker, campagne)
    assert len(_consommes(campagne)) == 6  # consommé à la soumission
    for message in messages[:3]:
        _accuser(message, Etat.NON_LIVRE)
    diffusion.publier()
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.TERMINEE and campagne.cout_reel == 6 * PRIX
    assert Reservation.objects.get(campagne_id=campagne.id).etat == Reservation.Etat.REGLEE


def test_le_cout_consomme_est_celui_fige_sur_le_message(client, worker):
    _livraison(client)
    campagne = _confirmee(client, 2)
    messages = _diffuser(worker, campagne)
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=PRIX * 10):  # la grille change
        for message in messages:
            _accuser(message, Etat.LIVRE)
        diffusion.publier()
    assert [montant for _, montant in _consommes(campagne)] == [-PRIX, -PRIX]


def test_un_changement_de_mode_en_cours_de_campagne_ne_change_rien(client, worker):
    campagne = _confirmee(client, 3)  # réservée à la soumission
    _livraison(client)
    _diffuser(worker, campagne)
    assert len(_consommes(campagne)) == 3
    diffusion.publier()
    campagne.refresh_from_db()
    assert campagne.cout_reel == 3 * PRIX
    assert Reservation.objects.get(campagne_id=campagne.id).etat == Reservation.Etat.REGLEE


def test_une_campagne_annulee_a_la_livraison_rend_ce_qui_n_est_pas_livre(client, worker):
    from apps.campagnes import services

    _livraison(client)
    campagne = _confirmee(client, 5)
    diffusion.publier()
    file = _en_file(campagne)
    for donnees in file[:2]:
        worker._soumettre(donnees)
    services.annuler(campagne, auteur=None)
    for donnees in file[2:]:
        worker._soumettre(donnees)  # retenus, annulés
    diffusion.publier()
    soumis = list(Message.objects.filter(campagne_id=campagne.id, etat=Etat.SOUMIS))
    assert len(soumis) == 2
    _accuser(soumis[0], Etat.LIVRE)
    _accuser(soumis[1], Etat.NON_LIVRE)
    diffusion.publier()

    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.ANNULEE and campagne.cout_reel == PRIX
    assert Reservation.objects.get(campagne_id=campagne.id).etat == Reservation.Etat.LIBEREE
    assert credits.solde(client.id) == credits.Solde(ACHAT - PRIX, 0)
