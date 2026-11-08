"""Webhooks sortants (T-111).

Une signature valide avec un horodatage de plus de cinq minutes est rejetée par
le récepteur de test. Un point de réception qui répond en erreur cinq fois est
désactivé et son propriétaire prévenu. Le journal de livraison est consultable
par le client lui-même.
"""

import json
import socket
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from django.core import mail
from django.db import transaction
from django.utils import timezone

from apps.api import webhooks
from apps.api.models import Livraison, PointReception
from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.chiffrement import dechiffrer
from apps.core.models import EvenementSortant

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

LIVRE, ECHEC, SOLDE_BAS = (
    PointReception.Evenement.LIVRE,
    PointReception.Evenement.ECHEC,
    PointReception.Evenement.SOLDE_BAS,
)


class Recepteur:
    """Récepteur de test : vérifie la signature comme le ferait un client, et
    répond par les statuts qu'on lui prépare, 204 ensuite."""

    def __init__(self):
        self.recus = []
        self.statuts = []
        self.secret = None
        recepteur = self

        class Gestionnaire(BaseHTTPRequestHandler):
            def do_POST(self):
                corps = self.rfile.read(int(self.headers["Content-Length"]))
                valide = recepteur.secret is not None and webhooks.verifier_signature(
                    recepteur.secret, corps, self.headers.get("Signature", "")
                )
                recepteur.recus.append((self.headers, corps, valide))
                statut = recepteur.statuts.pop(0) if recepteur.statuts else 204
                self.send_response(statut)
                if 300 <= statut < 400:
                    self.send_header("Location", "http://127.0.0.1:9/ailleurs")
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.serveur = ThreadingHTTPServer(("127.0.0.1", 0), Gestionnaire)
        self.url = f"http://127.0.0.1:{self.serveur.server_port}/webhooks"
        threading.Thread(target=self.serveur.serve_forever, daemon=True).start()

    def fermer(self):
        self.serveur.shutdown()
        self.serveur.server_close()


@pytest.fixture
def recepteur():
    serveur = Recepteur()
    yield serveur
    serveur.fermer()


@pytest.fixture
def compte(espaces, settings):
    """Un compte et son administrateur, sous le rôle des workers."""
    settings.WEBHOOKS_ADRESSES_PRIVEES = True
    espaces("worker")
    client = creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)
    admin = creer_utilisateur(
        courriel="awa@fasoriz.bf",
        mot_de_passe="x-1234-abcd",
        nom="Awa",
        client=client,
        role=Utilisateur.Role.ADMIN,
        courriel_verifie_le=timezone.now(),
    )
    return client, admin


def _point(compte, url, evenements=(LIVRE,)):
    client, admin = compte
    return webhooks.creer_point(client=client, url=url, evenements=list(evenements), auteur=admin)


def _evenement(client, nom=LIVRE):
    return {
        "nom": nom,
        "client_id": str(client.id),
        "objet_id": None,
        "auteur_id": None,
        "donnees": {"message_id": str(uuid.uuid7()), "etat": "LIVRE"},
        "cle_idempotence": f"webhook:{uuid.uuid7()}:LIVRE",
    }


def _repartir(evenement):
    # Le worker répartit dans la transaction de `traiter_une_fois`.
    with transaction.atomic():
        return webhooks.repartir(evenement)


def test_une_signature_de_plus_de_cinq_minutes_est_rejetee():
    corps, instant = b'{"id":"x"}', 1_800_000_000
    entete = webhooks.signer("whsec_essai", corps, instant)
    assert webhooks.verifier_signature("whsec_essai", corps, entete, maintenant=instant + 299)
    for decalage in (301, -301):
        assert not webhooks.verifier_signature(
            "whsec_essai", corps, entete, maintenant=instant + decalage
        )
    # Corps retouché, autre secret, en-têtes abîmés : refusés.
    assert not webhooks.verifier_signature("whsec_essai", corps + b" ", entete, maintenant=instant)
    assert not webhooks.verifier_signature("autre", corps, entete, maintenant=instant)
    for abime in ("", "v1=abc", "t=x,v1=abc", f"t={instant},abc", f"t={instant}"):
        assert not webhooks.verifier_signature("whsec_essai", corps, abime, maintenant=instant)


def test_un_point_se_declare_avec_un_secret_montre_une_seule_fois(compte, recepteur, settings):
    point, secret = _point(compte, recepteur.url, (LIVRE, ECHEC))
    assert secret.startswith("whsec_")
    # Gardé chiffré, jamais en clair.
    assert secret not in point.secret_chiffre and dechiffrer(point.secret_chiffre) == secret
    assert point.evenements == sorted([LIVRE, ECHEC])
    for url, evenements in (
        (recepteur.url, ()),
        (recepteur.url, ("client.inconnu",)),
        ("ftp://exemple.bf/x", (LIVRE,)),
        ("https://moi:secret@exemple.bf/x", (LIVRE,)),
    ):
        with pytest.raises(webhooks.PointRefuse):
            _point(compte, url, evenements)
    # En production : https seulement, et jamais une adresse interne.
    settings.WEBHOOKS_ADRESSES_PRIVEES = False
    for url in (
        recepteur.url,
        "https://127.0.0.1/x",
        "https://10.1.2.3/x",
        "https://192.168.1.10/x",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/x",
    ):
        with pytest.raises(webhooks.PointRefuse):
            _point(compte, url)


def test_un_evenement_donne_une_livraison_par_point_abonne_et_une_seule(compte, recepteur):
    client, admin = compte
    abonne, _ = _point(compte, recepteur.url, (LIVRE, ECHEC))
    _point(compte, recepteur.url, (SOLDE_BAS,))
    eteint, _ = _point(compte, recepteur.url, (LIVRE,))
    webhooks.desactiver_point(eteint, auteur=admin)
    evenement = _evenement(client)
    (livraison,) = _repartir(evenement)
    assert livraison.point_id == abonne.id and livraison.evenement == LIVRE
    assert livraison.corps["id"] == evenement["cle_idempotence"]
    assert livraison.corps["type"] == LIVRE
    assert livraison.corps["donnees"] == evenement["donnees"]
    assert EvenementSortant.objects.filter(
        cle_idempotence=f"livraison:{livraison.id}", nom__endswith="webhooks.sortants"
    ).exists()
    # Le même événement relivré par le courtier ne crée rien de plus.
    assert _repartir(evenement) == []
    assert Livraison.objects.count() == 1
    # Un événement qui n'est pas destiné aux clients, ou sans client : rien.
    assert _repartir({**_evenement(client), "nom": "journal.accuse.livre"}) == []
    assert _repartir({**_evenement(client), "client_id": None}) == []


def test_une_livraison_signee_est_recue_et_verifiee_par_le_recepteur(compte, recepteur):
    client, _ = compte
    _, secret = _point(compte, recepteur.url)
    recepteur.secret = secret
    (livraison,) = _repartir(_evenement(client))
    livraison = webhooks.livrer(livraison.id)
    assert livraison.etat == Livraison.Etat.LIVREE and livraison.livree_le
    assert (livraison.tentatives, livraison.dernier_statut_http) == (1, 204)
    ((entetes, corps, valide),) = recepteur.recus
    assert valide
    assert json.loads(corps)["type"] == LIVRE
    assert entetes.get("Webhook-Id") == livraison.cle_evenement
    assert entetes.get("Webhook-Evenement") == LIVRE
    # Le même envoi, intercepté et rejoué six minutes plus tard, est refusé.
    assert not webhooks.verifier_signature(
        secret, corps, entetes.get("Signature"), maintenant=time.time() + 360
    )
    # Une livraison déjà livrée ne repart pas.
    webhooks.livrer(livraison.id)
    assert len(recepteur.recus) == 1


def test_un_succes_remet_le_compteur_d_echecs_a_zero(compte, recepteur):
    client, _ = compte
    point, _ = _point(compte, recepteur.url)
    recepteur.statuts = [500, 500]
    (livraison,) = _repartir(_evenement(client))
    for _ in range(2):
        with pytest.raises(webhooks.LivraisonEchouee):
            webhooks.livrer(livraison.id)
    point.refresh_from_db()
    assert point.echecs_consecutifs == 2
    # Le palier suivant ramène l'ordre : cette fois le point répond.
    assert webhooks.livrer(livraison.id).etat == Livraison.Etat.LIVREE
    point.refresh_from_db()
    assert point.echecs_consecutifs == 0 and point.actif


def test_cinq_echecs_desactivent_le_point_et_previennent_son_proprietaire(compte, recepteur):
    client, admin = compte
    point, _ = _point(compte, recepteur.url)
    # Erreurs serveur, redirection (jamais suivie), page absente.
    recepteur.statuts = [500, 503, 302, 500, 404]
    livraisons = [livraison for _ in range(6) for livraison in _repartir(_evenement(client))]
    for livraison in livraisons[:4]:
        with pytest.raises(webhooks.LivraisonEchouee):
            webhooks.livrer(livraison.id)
    point.refresh_from_db()
    assert point.actif and point.echecs_consecutifs == 4 and not mail.outbox
    with pytest.raises(webhooks.LivraisonEchouee):
        webhooks.livrer(livraisons[4].id)
    point.refresh_from_db()
    assert not point.actif and point.desactive_le and point.echecs_consecutifs == 5
    (courriel,) = mail.outbox
    assert courriel.to == [admin.courriel] and point.url in courriel.body
    statuts = [Livraison.objects.get(id=lv.id).dernier_statut_http for lv in livraisons[:5]]
    assert statuts == [500, 503, 302, 500, 404]
    # Une livraison encore en attente vers ce point est abandonnée sans tentative.
    assert webhooks.livrer(livraisons[5].id).etat == Livraison.Etat.REBUT
    assert len(recepteur.recus) == 5
    # Après le dernier palier, le worker met la livraison au rebut.
    abandonnee = webhooks.abandonner(livraisons[0].id, "toutes les tentatives ont échoué")
    assert abandonnee.etat == Livraison.Etat.REBUT


def test_un_point_injoignable_est_un_echec_et_non_une_panne(compte):
    client, _ = compte
    # Un port libéré aussitôt : personne n'y écoute.
    with socket.socket() as prise:
        prise.bind(("127.0.0.1", 0))
        port = prise.getsockname()[1]
    _point(compte, f"http://127.0.0.1:{port}/webhooks")
    (livraison,) = _repartir(_evenement(client))
    with pytest.raises(webhooks.LivraisonEchouee):
        webhooks.livrer(livraison.id)
    livraison.refresh_from_db()
    assert livraison.tentatives == 1 and livraison.dernier_statut_http is None
    assert livraison.derniere_erreur and livraison.etat == Livraison.Etat.EN_COURS


def test_le_seuil_de_solde_bas_part_une_fois_puis_se_rearme(compte):
    from apps.comptes.services import regler_seuil_alerte
    from apps.core.cloisonnement import poser_client
    from apps.credits import services as credits

    client, admin = compte

    def alertes():
        return EvenementSortant.objects.filter(nom=SOLDE_BAS, client_id=client.id).count()

    with transaction.atomic():
        poser_client(client.id)
        credits.acheter(client.id, 10_000, reference="OM-essai")
        regler_seuil_alerte(client, "5000", auteur=admin)
        # 10 000 puis 7 000 : au-dessus du seuil, rien.
        credits.reserver(client.id, 3_000, campagne_id=uuid.uuid7())
        assert alertes() == 0
        # 4 000 : sous le seuil, une alerte ; 3 000 : toujours dessous, pas de seconde.
        credits.reserver(client.id, 3_000, campagne_id=uuid.uuid7())
        credits.reserver(client.id, 1_000, campagne_id=uuid.uuid7())
        assert alertes() == 1
        # Une recharge à 5 500 ne réarme pas (seuil majoré de vingt pour cent : 6 000).
        credits.acheter(client.id, 2_500, reference="OM-essai-2")
        credits.reserver(client.id, 1_000, campagne_id=uuid.uuid7())
        assert alertes() == 1
        # Recharge à 14 500 : réarmée ; la descente suivante sous le seuil alerte.
        credits.acheter(client.id, 10_000, reference="OM-essai-3")
        credits.reserver(client.id, 10_000, campagne_id=uuid.uuid7())
        assert alertes() == 2
        # Par la réservation groupée de l'API aussi : 4 500, rechargé à 14 500,
        # puis deux messages de 5 000 ramènent à 4 500.
        credits.acheter(client.id, 10_000, reference="OM-essai-4")
        maintenant = timezone.now()
        lignes = [credits.LigneReservation(uuid.uuid7(), maintenant, 5_000) for _ in range(2)]
        assert credits.reserver_messages(client.id, lignes) == 2
        assert alertes() == 3
    with pytest.raises(ValueError):
        regler_seuil_alerte(client, "-1", auteur=admin)
    with pytest.raises(ValueError):
        regler_seuil_alerte(client, "12,5", auteur=admin)


def test_le_client_gere_ses_points_et_lit_son_journal_de_livraison(
    client, compte, recepteur, espaces
):
    compte_client, admin = compte
    autre = creer_client(raison_sociale="Autre SARL", forme=Client.Forme.SOCIETE)
    _, _ = webhooks.creer_point(client=autre, url=recepteur.url, evenements=[LIVRE], auteur=None)
    _repartir(_evenement(autre))
    espaces("client")
    client.force_login(admin)
    page = client.post("/compte/webhooks", {"url": recepteur.url, "evenements": [LIVRE, ECHEC]})
    assert page.status_code == 200 and page["Cache-Control"] == "no-store"
    assert b"whsec_" in page.content
    espaces("worker")
    point = PointReception.objects.get(client=compte_client)
    _repartir(_evenement(compte_client))
    espaces("client")
    client.force_login(admin)
    # Le journal ne montre que les livraisons du compte.
    journal = client.get("/compte/webhooks/livraisons")
    assert journal.status_code == 200
    assert list(journal.context["livraisons"]) == list(
        Livraison.objects.using("worker").filter(point=point)
    )
    # Désactivation et réactivation par l'écran.
    client.post(f"/compte/webhooks/{point.id}/desactiver")
    assert not PointReception.objects.using("worker").get(id=point.id).actif
    client.post(f"/compte/webhooks/{point.id}/reactiver")
    assert PointReception.objects.using("worker").get(id=point.id).actif
    # Le point d'un autre compte reste introuvable.
    point_autre = PointReception.objects.using("worker").get(client=autre)
    assert client.post(f"/compte/webhooks/{point_autre.id}/desactiver").status_code == 404
    # Le seuil d'alerte se règle sur la même page.
    client.post("/compte/alerte-solde", {"seuil": "2500"})
    assert Client.objects.using("worker").get(id=compte_client.id).seuil_alerte_solde == 2500
