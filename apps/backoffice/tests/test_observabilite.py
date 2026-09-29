"""Journaux et métriques, T-16."""

import io
import json
import logging

import pytest
from django.db import connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.journalisation import FiltreContexte, FormatJson, contexte
from apps.core.redis import client_redis
from apps.journal.services import CLE_SOUMIS, enregistrer_message, soumettre
from apps.telecom import correlation
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import passerelle_pour
from apps.telecom.supervision import publier_etat_liaisons

JETON = "jeton-de-test-des-metriques"


@pytest.fixture
def sortie_json():
    """Branche le vrai format JSON et le vrai filtre sur la racine des journaux."""
    tampon = io.StringIO()
    gestionnaire = logging.StreamHandler(tampon)
    gestionnaire.setFormatter(FormatJson())
    gestionnaire.addFilter(FiltreContexte())
    racine = logging.getLogger()
    racine.addHandler(gestionnaire)
    yield lambda: [json.loads(ligne) for ligne in tampon.getvalue().splitlines()]
    racine.removeHandler(gestionnaire)


@pytest.fixture
def jeton(settings):
    settings.METRIQUES_JETON = JETON
    return {"HTTP_AUTHORIZATION": f"Bearer {JETON}"}


@pytest.mark.django_db
def test_les_lignes_d_une_requete_portent_le_meme_identifiant(client, jeton, sortie_json):
    reponse = client.get("/metriques", **jeton)
    identifiant = reponse["X-Request-ID"]
    lignes = [ligne for ligne in sortie_json() if ligne.get("requete_id") == identifiant]
    assert len(identifiant) == 32
    assert {"Métriques servies."} <= {ligne["message"] for ligne in lignes}
    assert all({"horodatage", "niveau", "journal"} <= set(ligne) for ligne in lignes)


@pytest.mark.django_db
def test_un_identifiant_de_requete_sain_est_repris_et_un_hostile_remplace(client, jeton):
    reponse = client.get("/metriques", HTTP_X_REQUEST_ID="frontal-1234abcd", **jeton)
    assert reponse["X-Request-ID"] == "frontal-1234abcd"
    reponse = client.get("/metriques", HTTP_X_REQUEST_ID='x"\n<script>', **jeton)
    assert reponse["X-Request-ID"] != 'x"\n<script>'


def test_une_ligne_porte_le_contexte_du_message(sortie_json):
    with contexte(requete_id="r1", client_id="c1", message_id="m1", connecteur_id="k1"):
        logging.getLogger("apps.test").info("Accusé reçu")
    ligne = next(ligne for ligne in sortie_json() if ligne["message"] == "Accusé reçu")
    assert (ligne["client_id"], ligne["message_id"], ligne["connecteur_id"]) == ("c1", "m1", "k1")


@pytest.mark.django_db
@pytest.mark.parametrize("entete", [None, "Bearer mauvais", f"Basic {JETON}", "Bearer "])
def test_les_metriques_sont_refusees_sans_le_bon_jeton(client, jeton, entete):
    options = {"HTTP_AUTHORIZATION": entete} if entete else {}
    assert client.get("/metriques", **options).status_code == 401


@pytest.mark.django_db
def test_les_metriques_sont_refusees_si_aucun_jeton_n_est_configure(client, settings):
    settings.METRIQUES_JETON = ""
    assert client.get("/metriques", HTTP_AUTHORIZATION="Bearer ").status_code == 401


# Seule la connexion `default` est permise : le test échoue si la vue touche
# à la connexion worker ou lit la table des connecteurs.
@pytest.mark.django_db(transaction=True)
def test_les_metriques_exposent_files_liaisons_et_debit(client, jeton, en_tant_que_worker):
    connecteur = Connecteur.objects.create(
        nom=f"orange-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
    )
    passerelle = passerelle_pour(connecteur)
    cle_compteur = CLE_SOUMIS.format(connecteur_id=connecteur.id)
    client_redis().delete(cle_compteur)
    references = []
    for rang in range(3):
        with transaction.atomic():
            message = enregistrer_message(
                client=creer_client(raison_sociale=f"C{rang}", forme=Client.Forme.SOCIETE),
                numero_normalise=f"+2267000000{rang}",
                operateur="ORANGE",
                expediteur_libelle="EXEMPLE",
                texte="Bonjour",
                cout_unitaire=10,
            )
            references.append(soumettre(message, passerelle).reference_smsc)

    publier_etat_liaisons()
    try:
        with CaptureQueriesContext(connections["default"]) as requetes:
            reponse = client.get("/metriques", **jeton)
    finally:
        client_redis().delete(cle_compteur)
        for reference in references:
            client_redis().delete(correlation.cle_redis(connecteur.id, reference))

    assert reponse.status_code == 200
    assert reponse["Content-Type"].startswith("text/plain")
    texte = reponse.content.decode()
    assert "sms_courtier_joignable 1" in texte
    assert 'sms_file_profondeur{file="envoi.masse.orange"}' in texte
    base = f'connecteur="{connecteur.nom}",operateur="ORANGE"'
    assert f'sms_liaison_etat{{{base},etat="CONNECTE"}} 1' in texte
    assert f'sms_liaison_etat{{{base},etat="DECONNECTE"}} 0' in texte
    assert f'sms_messages_soumis_total{{connecteur="{connecteur.nom}"}} 3' in texte
    assert "sms_liaisons_age_secondes -1" not in texte
    assert not [r for r in requetes.captured_queries if "telecom_connecteur" in r["sql"]]
