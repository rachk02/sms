"""Écrans des modèles de message (T-55, T-57).

Le test lit la base par la connexion du worker : sous le rôle applicatif, sans
client posé, le cloisonnement lui masquerait tout."""

import json
import re

import pytest
from django.utils import timezone

from apps.campagnes.models import Modele
from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts.models import ChampPersonnalise

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def membre():
    compte = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    ChampPersonnalise.objects.using("worker").create(
        client=compte, nom="code_client", libelle="Code client", valeur_repli="à venir"
    )
    return creer_utilisateur(
        courriel="u@exemple.bf",
        mot_de_passe="x-1234-abcd",
        nom="U",
        client=compte,
        role=Utilisateur.Role.UTILISATEUR,
        courriel_verifie_le=timezone.now(),
    )


def _json(page, identifiant):
    trouve = re.search(rf'<script id="{identifiant}" type="application/json">(.*?)</script>', page)
    return json.loads(trouve.group(1))


def test_l_editeur_recoit_les_tables_du_serveur_et_les_replis_declares(client, membre):
    client.force_login(membre)
    page = client.get("/campagnes/modeles").content.decode()
    assert _json(page, "table-encodage")["gsm_simple"] == 160
    assert _json(page, "replis-declares") == {"code_client": "à venir"}
    assert "js/encodage.js" in page and "js/compositeur.js" in page
    assert page.index("js/compositeur.js") < page.index("alpine-")


def test_un_modele_sans_repli_est_refuse_avec_son_explication(client, membre):
    client.force_login(membre)
    page = client.post(
        "/campagnes/modeles", {"nom": "Relance", "texte": "Bonjour {prenom}"}
    ).content.decode()
    assert "demande une valeur de repli" in page
    assert _json(page, "texte-initial") == "Bonjour {prenom}"  # la saisie n'est pas perdue
    assert not Modele.objects.using("worker").exists()


def test_un_modele_se_cree_puis_se_modifie(client, membre):
    client.force_login(membre)
    texte = "Bonjour {prenom|cher client}, code {code_client}."
    client.post("/campagnes/modeles", {"nom": "Relance", "texte": texte})
    modele = Modele.objects.using("worker").get()
    assert modele.variables == ["prenom", "code_client"]
    page = client.get(f"/campagnes/modeles/{modele.id}").content.decode()
    assert _json(page, "texte-initial") == texte
    client.post(
        f"/campagnes/modeles/{modele.id}", {"nom": "Relance", "texte": "Merci {nom|client}"}
    )
    assert Modele.objects.using("worker").get().variables == ["nom"]
