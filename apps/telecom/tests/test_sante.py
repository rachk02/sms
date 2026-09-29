"""Santé des connecteurs, T-29."""

import socket
import subprocess
import time
from datetime import timedelta

import pytest
from django.conf import settings
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.models import EvenementSortant
from apps.journal.services import appliquer_accuse, enregistrer_message, soumettre
from apps.telecom import correlation, sante
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import EtatLiaison, passerelle_pour
from workers.ordonnanceur import Ordonnanceur

pytestmark = pytest.mark.django_db(transaction=True)


@pytest.fixture
def connecteur(en_tant_que_worker):
    connecteur = Connecteur.objects.create(
        nom=f"c-{timezone.now().timestamp()}",
        operateur=Operateur.MOOV,
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        parametres={"taux_echec": 0.8, "graine": 7},
    )
    yield connecteur
    sante.client_redis().delete(sante.CLE.format(connecteur_id=connecteur.id))


def _alertes(connecteur):
    return list(
        EvenementSortant.objects.filter(objet_id=connecteur.id)
        .order_by("cree_le")
        .values_list("nom", flat=True)
    )


def test_un_taux_d_echec_a_80_pour_cent_degrade_la_liaison_alors_que_la_session_tient(
    connecteur,
):
    passerelle = passerelle_pour(connecteur)
    client = creer_client(
        raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    references = []
    for rang in range(50):
        message = enregistrer_message(
            client=client,
            numero_normalise=f"+2266{rang:07d}",
            operateur="MOOV",
            expediteur_libelle="EXEMPLE",
            texte="Bonjour",
            cout_unitaire=10,
        )
        references.append(soumettre(message, passerelle).reference_smsc)
    for accuse in passerelle.accuses_dus():
        appliquer_accuse(accuse)
    for reference in references:
        correlation._redis().delete(correlation.cle_redis(connecteur.id, reference))

    bilan = sante.evaluer(passerelle)
    assert bilan.session == EtatLiaison.CONNECTE
    assert bilan.mesures.total == 50 and bilan.mesures.taux < 0.35
    assert bilan.etat == EtatLiaison.DEGRADE and "sous le seuil" in bilan.motif

    sante.surveiller()
    connecteur.refresh_from_db()
    assert connecteur.etat_liaison == EtatLiaison.DEGRADE
    assert _alertes(connecteur) == ["telecom.liaison.degradee"]


def test_une_poignee_d_echecs_sur_un_trafic_faible_ne_degrade_pas(connecteur):
    maintenant = timezone.now()
    for _ in range(sante.ECHANTILLON_MIN - 1):
        sante.enregistrer_accuse(connecteur.id, False, 2.0, maintenant)
    assert sante.evaluer(passerelle_pour(connecteur), maintenant).etat == EtatLiaison.CONNECTE


def test_la_fenetre_glisse_et_le_delai_moyen_suit(connecteur):
    maintenant = timezone.now()
    for _ in range(30):
        sante.enregistrer_accuse(connecteur.id, False, 9.0, maintenant - timedelta(minutes=10))
    for delai in (2.0, 4.0):
        sante.enregistrer_accuse(connecteur.id, True, delai, maintenant)
    releve = sante.mesures(connecteur.id, maintenant)
    assert (releve.total, releve.positifs, releve.delai_moyen_s) == (2, 2, 3.0)


def test_une_session_tombee_alerte_une_fois_puis_le_retablissement(connecteur):
    connecteur.parametres = {"coupe": True}
    connecteur.save()
    sante.surveiller()
    sante.surveiller()
    connecteur.refresh_from_db()
    assert connecteur.etat_liaison == EtatLiaison.DECONNECTE
    assert _alertes(connecteur) == ["telecom.liaison.tombee"]

    connecteur.parametres = {}
    connecteur.save()
    sante.surveiller()
    connecteur.refresh_from_db()
    assert connecteur.etat_liaison == EtatLiaison.CONNECTE
    assert connecteur.derniere_connexion_le is not None
    assert _alertes(connecteur) == ["telecom.liaison.tombee", "telecom.liaison.retablie"]


def test_l_ordonnanceur_surveille_assez_souvent_pour_alerter_en_moins_d_une_minute():
    assert Ordonnanceur.periode_supervision_s <= 30


def _jasmin_lance() -> bool:
    try:
        socket.create_connection(("127.0.0.1", settings.JASMIN_JCLI_PORT), timeout=1).close()
        return bool(settings.JASMIN_JCLI_USER)
    except OSError:
        return False


@pytest.mark.skipif(not _jasmin_lance(), reason="Jasmin n'est pas lancé (profil passerelle)")
def test_couper_le_simulateur_smpp_declenche_l_alerte_en_moins_d_une_minute(en_tant_que_worker):
    connecteur = Connecteur.objects.create(
        nom=f"jasmin-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SMPP,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        parametres={"cid_jasmin": "simulateur"},
    )
    assert sante.evaluer(passerelle_pour(connecteur)).session == EtatLiaison.CONNECTE

    subprocess.run(["docker", "compose", "stop", "smsc"], check=True, capture_output=True)
    debut = time.monotonic()
    try:
        while time.monotonic() - debut < 60:
            sante.surveiller()
            connecteur.refresh_from_db()
            if connecteur.etat_liaison == EtatLiaison.DECONNECTE:
                break
            time.sleep(Ordonnanceur.periode_supervision_s / 3)
        delai = time.monotonic() - debut
    finally:
        subprocess.run(["docker", "compose", "start", "smsc"], check=True, capture_output=True)
        # Rendre la liaison reliée : les tests suivants qui passent par Jasmin
        # ne doivent pas hériter d'une session en cours de reconnexion.
        fin = time.monotonic() + 90
        while time.monotonic() < fin:
            if passerelle_pour(connecteur).sonder() == EtatLiaison.CONNECTE:
                break
            time.sleep(2)

    assert connecteur.etat_liaison == EtatLiaison.DECONNECTE, "aucune alerte en une minute"
    assert delai < 60
    assert _alertes(connecteur) == ["telecom.liaison.tombee"]


def test_une_route_degradee_ne_se_retablit_qu_a_90_pour_cent(connecteur):
    maintenant = timezone.now()
    for rang in range(40):
        sante.enregistrer_accuse(connecteur.id, rang % 100 < 35, 2.0, maintenant)  # 35 sur 40
    connecteur.etat_liaison = EtatLiaison.CONNECTE
    assert sante.evaluer(passerelle_pour(connecteur), maintenant).etat == EtatLiaison.CONNECTE
    connecteur.etat_liaison = EtatLiaison.DEGRADE
    # 87,5 % : au-dessus du seuil d'alerte, sous celui de rétablissement.
    assert sante.evaluer(passerelle_pour(connecteur), maintenant).etat == EtatLiaison.DEGRADE
