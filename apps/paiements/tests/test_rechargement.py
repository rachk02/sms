"""Parcours de rechargement en ligne (T-86).

Le test lit la base par la connexion du worker : sous le rôle applicatif, sans
client posé, le cloisonnement lui masquerait tout.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.test import override_settings
from django.utils import timezone

from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.redis import client_redis
from apps.credits.models import Ecriture
from apps.paiements import fournisseurs, services
from apps.paiements.models import Reglage, Transaction

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

Etat = Transaction.Etat


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


def _membre(role=Utilisateur.Role.ADMIN, courriel="admin@fasoriz.bf"):
    compte = creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)
    return creer_utilisateur(
        courriel=courriel,
        mot_de_passe="x-1234-abcd",
        nom="Awa",
        client=compte,
        role=role,
        courriel_verifie_le=timezone.now(),
    )


def test_les_credits_s_achetent_par_multiples_qui_tombent_juste():
    avec_tva = Reglage(tva_active=True, taux_tva=18)
    assert services.pas_d_achat(avec_tva) == 50
    assert services.montants(10_000, avec_tva) == services.Montants(10_000, 1_800, 11_800)
    assert services.montants(50, avec_tva) == services.Montants(50, 9, 59)
    for refuse in (125, 0, -50, True):
        with pytest.raises(services.RechargementRefuse):
            services.montants(refuse, avec_tva)

    sans_tva = Reglage(tva_active=False, taux_tva=18)
    assert services.pas_d_achat(sans_tva) == 1
    assert services.montants(125, sans_tva) == services.Montants(125, 0, 125)

    bornes = Reglage(tva_active=True, taux_tva=18, credits_min=1_000, credits_max=5_000)
    for hors in (950, 5_050):
        with pytest.raises(services.RechargementRefuse):
            services.montants(hors, bornes)


def test_un_paiement_confirme_navigateur_ferme_est_credite_par_la_relance(en_tant_que_worker):
    """Le client a payé puis fermé son navigateur, et le rappel s'est perdu : la
    relance de l'ordonnanceur interroge le fournisseur, crédite et prévient."""
    membre = _membre()
    transaction_, adresse = services.initier(
        membre.client, credits_ht=10_000, fournisseur_nom="simule", auteur=membre
    )
    assert transaction_.etat == Etat.EN_ATTENTE and transaction_.montant == 11_800
    assert adresse == f"/paiements/simulateur/{transaction_.reference_transaction}"
    services.relancer()
    assert not Ecriture.objects.filter(client_id=membre.client_id).exists()

    fournisseurs.simuler(transaction_.reference_transaction, montant=11_800, etat="CONFIRME")
    services.relancer()

    transaction_.refresh_from_db()
    assert transaction_.etat == Etat.CONFIRME
    (achat,) = Ecriture.objects.filter(client_id=membre.client_id, type=Ecriture.Type.ACHAT)
    assert achat.montant == 10_000
    (courriel,) = mail.outbox
    assert courriel.to == [membre.courriel] and "10000 FCFA de crédits" in courriel.body


def test_au_dela_du_delai_le_paiement_expire_puis_se_reprend(en_tant_que_worker):
    membre = _membre()
    transaction_, _ = services.initier(
        membre.client, credits_ht=1_000, fournisseur_nom="simule", auteur=membre
    )
    assert services.relancer(maintenant=transaction_.expire_le + timedelta(seconds=1)) == 1
    transaction_.refresh_from_db()
    assert transaction_.etat == Etat.EXPIRE

    # Confirmé ensuite chez le fournisseur : un rappel tardif le reprend.
    fournisseurs.simuler(transaction_.reference_transaction, montant=1_180, etat="CONFIRME")
    services.verifier("SIMULE", transaction_.reference_transaction)
    transaction_.refresh_from_db()
    assert transaction_.etat == Etat.CONFIRME


def test_la_page_d_attente_s_arrete_au_delai_et_dit_l_etat_reel(client):
    membre = _membre()
    client.force_login(membre)
    reponse = client.post("/paiements/recharger", {"credits": "1000", "fournisseur": "simule"})
    transaction_ = Transaction.objects.using("worker").get()
    assert reponse["Location"] == f"/paiements/simulateur/{transaction_.reference_transaction}"

    # Le client paie, mais le rappel du fournisseur se perd.
    retour = client.post(f"{reponse['Location']}/choix", {"choix": "payer_sans_rappel"})
    assert retour["Location"] == f"/paiements/{transaction_.id}"
    page = client.get(retour["Location"]).content.decode()
    assert "En attente de la confirmation" in page and "hx-get" in page

    Transaction.objects.using("worker").filter(id=transaction_.id).update(
        expire_le=timezone.now() - timedelta(seconds=1)
    )
    fragment = client.get(f"/paiements/{transaction_.id}/fragments/statut").content.decode()
    assert "Toujours sans confirmation" in fragment and "hx-get" not in fragment
    assert "Faire un autre rechargement" in fragment


def test_seul_un_administrateur_recharge_et_le_simulateur_coupe_disparait(client):
    utilisateur = _membre(role=Utilisateur.Role.UTILISATEUR, courriel="u@fasoriz.bf")
    client.force_login(utilisateur)
    assert client.get("/paiements/recharger").status_code == 403

    fournisseurs.simuler("SIM-0001", montant=1_180, etat="EN_ATTENTE")
    assert client.get("/paiements/simulateur/SIM-0001").status_code == 200
    with override_settings(PAIEMENTS_SIMULATEUR=False):
        assert client.get("/paiements/simulateur/SIM-0001").status_code == 404
        assert (
            client.post("/paiements/simulateur/SIM-0001/choix", {"choix": "payer"}).status_code
            == 404
        )


def test_le_back_office_coupe_la_tva_et_c_est_trace(en_tant_que_worker):
    from apps.core.models import Audit

    services.regler(Reglage(tva_active=False, taux_tva=18), agent=None)

    assert services.montants(125) == services.Montants(125, 0, 125)
    (trace,) = Audit.objects.filter(action="paiements.reglage.modifie")
    assert trace.valeur_avant is None and trace.valeur_apres["tva_active"] is False
