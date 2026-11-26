"""Configuration des connecteurs et des routes au back-office (T-97).

Un secret saisi n'est jamais réaffiché. Un changement de débit vaut dès le
message suivant, sans redémarrer les workers. Toute modification laisse sa
trace, avec l'ancienne et la nouvelle valeur.
"""

import json
import uuid

import pytest
from django.contrib.auth.models import Group

from apps.backoffice import roles
from apps.comptes.services import creer_utilisateur
from apps.core.chiffrement import dechiffrer
from apps.core.models import Audit
from apps.telecom import services as telecom
from apps.telecom.models import Connecteur, Operateur, Route
from workers.envoi import WorkerEnvoi

pytestmark = pytest.mark.django_db(transaction=True)

SECRET = "Pq7!zr2w"


@pytest.fixture
def agent(en_tant_que_backoffice):
    agent = creer_utilisateur(
        courriel="exploitation@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Exploitation",
        is_staff=True,
    )
    agent.groups.add(Group.objects.get(name=roles.EXPLOITATION))
    return agent


def _saisie(nom, **autres):
    return {
        "nom": nom,
        "operateur": "ORANGE",
        "type": "SMPP",
        "hote": "smsc.orange.test",
        "port": "2775",
        "identifiant": "elimmeka",
        "secret": "",
        "debit_max": "10",
        "sessions_max": "1",
        "fenetre": "10",
        "parametres": '{"cid_jasmin": "orange"}',
        "actif": "on",
        **autres,
    }


def test_un_secret_saisi_n_est_jamais_reaffiche(client, agent):
    client.force_login(agent)
    nom = f"orange-{uuid.uuid4().hex[:6]}"
    reponse = client.post("/admin/telecom/connecteur/add/", _saisie(nom, secret=SECRET))
    assert reponse.status_code == 302, reponse.content.decode()[:2000]
    connecteur = Connecteur.objects.get(nom=nom)
    assert connecteur.secret_chiffre and SECRET not in connecteur.secret_chiffre
    assert dechiffrer(connecteur.secret_chiffre) == SECRET

    page = client.get(f"/admin/telecom/connecteur/{connecteur.id}/change/").content.decode()
    assert SECRET not in page and connecteur.secret_chiffre not in page
    assert "Enregistré, chiffré" in page
    trace = Audit.objects.get(action="telecom.connecteur.creation")
    assert trace.valeur_apres["secret"] == "présent" and trace.auteur_id == agent.id
    assert SECRET not in json.dumps(trace.valeur_apres)
    assert connecteur.secret_chiffre not in json.dumps(trace.valeur_apres)

    # Modifié sans ressaisie : le secret reste ; l'audit porte avant et après.
    reponse = client.post(
        f"/admin/telecom/connecteur/{connecteur.id}/change/", _saisie(nom, debit_max="40")
    )
    assert reponse.status_code == 302
    connecteur.refresh_from_db()
    assert dechiffrer(connecteur.secret_chiffre) == SECRET
    trace = Audit.objects.get(action="telecom.connecteur.modification")
    assert (trace.valeur_avant["debit_max"], trace.valeur_apres["debit_max"]) == (10, 40)
    assert trace.valeur_apres["secret"] == "présent"

    # Ressaisi : remplacé, et l'audit le dit sans le montrer.
    client.post(
        f"/admin/telecom/connecteur/{connecteur.id}/change/", _saisie(nom, secret="Nv8?ab3c")
    )
    connecteur.refresh_from_db()
    assert dechiffrer(connecteur.secret_chiffre) == "Nv8?ab3c"
    derniere = Audit.objects.filter(action="telecom.connecteur.modification").latest("horodatage")
    assert derniere.valeur_apres["secret"] == "remplacé"
    assert "Nv8?ab3c" not in json.dumps(derniere.valeur_apres)


def test_un_reglage_qui_ressemble_a_un_secret_est_refuse(client, agent):
    client.force_login(agent)
    nom = f"moov-{uuid.uuid4().hex[:6]}"
    reponse = client.post(
        "/admin/telecom/connecteur/add/", _saisie(nom, parametres='{"password": "abc"}')
    )
    assert reponse.status_code == 200 and "ressemble à un secret" in reponse.content.decode()
    assert not Connecteur.objects.filter(nom=nom).exists()


def test_le_support_ne_configure_pas_les_connecteurs(client, agent):
    support = creer_utilisateur(
        courriel="support@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Support",
        is_staff=True,
    )
    support.groups.add(Group.objects.get(name=roles.SUPPORT))
    client.force_login(support)
    assert client.get("/admin/telecom/connecteur/add/").status_code == 403


def _connecteur(nom, operateur=Operateur.ORANGE, **champs):
    return Connecteur.objects.create(
        nom=f"{nom}-{uuid.uuid4().hex[:6]}",
        operateur=operateur,
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        **champs,
    )


def test_un_changement_de_debit_vaut_sans_redemarrer_les_workers(agent):
    connecteur = _connecteur("orange", debit_max=10)
    worker = WorkerEnvoi(files=[])
    avant = worker._passerelle(telecom.connecteur_pour("ORANGE"))
    assert avant.limites().messages_par_seconde == 10

    connecteur.debit_max = 50
    telecom.enregistrer_connecteur(connecteur, agent=agent)

    # Le worker relit le connecteur à chaque message et refait sa passerelle.
    relu = telecom.connecteur_pour("ORANGE")
    assert relu.debit_max == 50
    apres = worker._passerelle(relu)
    assert apres is not avant and apres.limites().messages_par_seconde == 50
    assert worker._passerelle(telecom.connecteur_pour("ORANGE")) is apres


def test_les_routes_servent_par_priorite(agent):
    principal = _connecteur("orange-direct")
    secours = _connecteur("moov-secours", operateur=Operateur.MOOV)
    sans_route = _connecteur("orange-ancien")
    # Sans route : le premier connecteur de l'opérateur, comme avant.
    assert telecom.connecteur_pour("ORANGE") == principal

    for priorite, connecteur in ((1, secours), (2, principal)):
        telecom.enregistrer_route(
            Route(operateur="ORANGE", connecteur=connecteur, priorite=priorite), agent=agent
        )
    assert telecom.connecteur_pour("ORANGE") == secours
    route = Route.objects.get(connecteur=secours)
    route.priorite = 3
    telecom.enregistrer_route(route, agent=agent)
    assert telecom.connecteur_pour("ORANGE") == principal
    trace = Audit.objects.get(action="telecom.route.modification")
    assert (trace.valeur_avant["priorite"], trace.valeur_apres["priorite"]) == (1, 3)

    # Liaison tombée : la route suivante sert ; toutes tombées : aucune, le
    # connecteur sans route n'est pas pris en douce.
    Connecteur.objects.filter(id=principal.id).update(
        etat_liaison=Connecteur.EtatLiaison.DECONNECTE
    )
    assert telecom.connecteur_pour("ORANGE") == secours
    Connecteur.objects.filter(id=secours.id).update(actif=False)
    assert telecom.connecteur_pour("ORANGE") is None
    assert sans_route.actif

    telecom.supprimer_route(route, agent=agent)
    trace = Audit.objects.get(action="telecom.route.suppression")
    assert trace.valeur_avant["connecteur"] == secours.nom
