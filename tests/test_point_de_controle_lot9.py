"""Point de contrôle du lot 9 (T-114).

Le script d'intégration (`scripts/integration_api.py`) joue une intégration
complète par la seule API : envoi par lot, coupure réseau en plein envoi puis
rejeu avec la même clé d'idempotence, journal, webhooks, dépassement de quota.
Ici, il parle à la plateforme par le client de test de Django au lieu du
réseau, et le test tient le rôle des workers entre l'envoi et la réception
(accusés simulés du bac à sable, répartition et livraison des webhooks).

Le script tourne sans accéder à l'interface web. La coupure rejouée ne produit
aucun doublon. Les webhooks reçus correspondent aux messages envoyés, un pour un.
"""

import importlib.util
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from django.utils import timezone

from apps.api import essais, webhooks
from apps.api.models import Livraison, MessageEssai, PointReception
from apps.backoffice.tests.test_clients import _preparer
from apps.comptes.models import CleApi
from apps.comptes.services import creer_cle
from apps.core.evenements import traiter_une_fois
from apps.core.models import EvenementSortant
from apps.journal.models import Message
from apps.telecom.models import Prefixe

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

_SPEC = importlib.util.spec_from_file_location(
    "integration_api", Path(__file__).resolve().parents[1] / "scripts" / "integration_api.py"
)
integration = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(integration)


def _transport_django(client, chemins):
    """Le transport du script, branché sur le client de test : mêmes
    requêtes, mêmes réponses, sans réseau. Note chaque chemin appelé."""

    def transporter(methode, url, corps, entetes):
        morceaux = urlsplit(url)
        chemin = morceaux.path + (f"?{morceaux.query}" if morceaux.query else "")
        chemins.append(morceaux.path)
        extra = {
            "HTTP_" + nom.upper().replace("-", "_"): valeur
            for nom, valeur in entetes.items()
            if nom.lower() != "content-type"
        }
        reponse = client.generic(
            methode,
            chemin,
            corps or b"",
            content_type=entetes.get("Content-Type", "application/octet-stream"),
            **extra,
        )
        return reponse.status_code, dict(reponse.items()), reponse.content

    return transporter


@pytest.fixture
def integrateur(espaces, settings):
    settings.SMS_PRIX_SEGMENT_PROVISOIRE = 20
    settings.SMS_NUMERO_COURT = "36000"
    settings.WEBHOOKS_ADRESSES_PRIVEES = True
    settings.API_QUOTAS = {"rafale_par_seconde": 5, "soutenu_par_minute": 1000}
    espaces("worker")
    compte = _preparer()
    # Préparation hors script, comme le ferait l'administrateur du compte :
    # une clé d'essai et un point de réception vers le récepteur du script.
    _, cle = creer_cle(
        client=compte, nom="Intégration", auteur=None, environnement=CleApi.Environnement.TEST
    )
    recepteur = integration.Recepteur("")
    _, secret = webhooks.creer_point(
        client=compte,
        url=recepteur.url,
        evenements=[PointReception.Evenement.LIVRE, PointReception.Evenement.ECHEC],
        auteur=None,
    )
    recepteur.secret = secret
    espaces("client")
    yield compte, cle, recepteur
    recepteur.fermer()
    espaces("worker")
    Prefixe.objects.filter(pays="BF").delete()


def _jouer_les_workers(espaces):
    """Ce que font l'ordonnanceur et le worker webhook entre l'envoi et la
    réception : accusés simulés, répartition des événements, livraisons."""

    def jouer():
        espaces("worker")
        plus_tard = timezone.now() + timedelta(seconds=30)
        while essais.avancer(plus_tard):
            pass
        for sortant in EvenementSortant.objects.filter(nom__startswith="client.message."):
            evenement = {
                "nom": sortant.nom,
                "client_id": str(sortant.client_id),
                "donnees": sortant.donnees,
                "cle_idempotence": sortant.cle_idempotence,
            }
            traiter_une_fois(
                "webhooks.evenements",
                sortant.cle_idempotence,
                lambda evenement=evenement: webhooks.repartir(evenement),
            )
        for livraison in Livraison.objects.filter(etat=Livraison.Etat.EN_COURS):
            webhooks.livrer(livraison.id)
        espaces("client")

    return jouer


def test_une_integration_complete_par_la_seule_api(client, integrateur, espaces):
    compte, cle, recepteur = integrateur
    chemins = []
    transport = _transport_django(client, chemins)
    api = integration.Api("http://testserver", cle, transport=transport)

    def couper(methode, url, corps, entetes):
        # La requête est traitée par la plateforme, la réponse se perd.
        transport(methode, url, corps, entetes)
        raise integration.CoupureReseau(url)

    api.transport_coupe = couper
    rapport = integration.scenario(
        api, recepteur, nombre=20, attendre_webhooks=_jouer_les_workers(espaces)
    )
    assert rapport == {
        "envoi": True,
        "rejeu_sans_doublon": True,
        "journal": True,
        "webhooks_un_pour_un": True,
        "quota": True,
    }
    # Sans l'interface web : seules les routes de l'API ont été appelées.
    assert chemins and all(chemin.startswith("/v1/") for chemin in chemins)
    assert api.refus_quota >= 1
    espaces("worker")
    # La coupure rejouée deux fois n'a écrit chaque message qu'une fois, et
    # rien n'est parti : aucun message au journal de production.
    assert MessageEssai.objects.filter(client=compte).count() == 20
    assert not Message.objects.filter(client_id=compte.id).exists()
    # Chaque webhook reçu a été livré une fois et vérifié.
    assert len(recepteur.sorts()) == 20 and recepteur.refuses == 0
