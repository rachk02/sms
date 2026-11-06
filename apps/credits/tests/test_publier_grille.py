"""Publication d'une grille par la ligne de commande (T-81, T-83).

Sans elle, rien hors des tests ne publierait de grille, et la recette, qui
n'a pas de prix de secours, refuserait tout envoi.
"""

import pytest
from django.core.management import CommandError, call_command
from django.test import override_settings

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.credits import tarifs
from apps.credits.models import Grille, VersionGrille
from apps.telecom.models import Prefixe

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

CSV = "pays,operateur,volume_min,prix_unitaire\nBF,ORANGE,0,20\nBF,ORANGE,10000,17\nBF,MOOV,0,19\n"


@pytest.fixture
def reseaux(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        [
            Prefixe(pays="BF", prefixe="70", operateur="ORANGE"),
            Prefixe(pays="BF", prefixe="60", operateur="MOOV"),
        ],
        ignore_conflicts=True,
    )
    yield
    Prefixe.objects.filter(pays="BF").delete()


@pytest.fixture
def admin(reseaux):
    return creer_utilisateur(
        courriel="admin@el-immeka.test", mot_de_passe="x-1234-abcd", nom="A", is_superuser=True
    )


def _fichier(tmp_path, texte=CSV):
    chemin = tmp_path / "prix.csv"
    chemin.write_text(texte, encoding="utf-8")
    return str(chemin)


def test_la_commande_publie_la_grille_publique(tmp_path, admin):
    call_command("publier_grille", _fichier(tmp_path), par="ADMIN@el-immeka.test")

    client = creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=None):
        assert tarifs.prix_segment(client.id, "ORANGE") == 20
        assert tarifs.prix_segment(client.id, "ORANGE", volume=12_000) == 17
        assert tarifs.prix_segment(client.id, "MOOV") == 19


def test_la_commande_publie_une_grille_negociee(tmp_path, admin):
    client = creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)
    texte = "pays,operateur,volume_min,prix_unitaire\nBF,ORANGE,0,14\n"
    call_command(
        "publier_grille",
        _fichier(tmp_path, texte),
        par=admin.courriel,
        nature="NEGOCIEE",
        client=str(client.id),
    )
    grille = Grille.objects.get(nature=Grille.Nature.NEGOCIEE)
    assert grille.client_id == client.id
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=None):
        assert tarifs.prix_segment(client.id, "ORANGE") == 14


def test_un_agent_qui_n_est_pas_super_administrateur_est_refuse(tmp_path, reseaux):
    creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x-1234-abcd", nom="B")
    with pytest.raises(CommandError, match="super-administrateur"):
        call_command("publier_grille", _fichier(tmp_path), par="agent@el-immeka.test")
    assert not Grille.objects.exists()


def test_une_erreur_ne_laisse_aucun_brouillon(tmp_path, admin):
    incomplet = "pays,operateur,volume_min,prix_unitaire\nBF,ORANGE,0,20\n"  # MOOV manque
    with pytest.raises(CommandError, match="MOOV"):
        call_command("publier_grille", _fichier(tmp_path, incomplet), par=admin.courriel)
    assert not Grille.objects.exists() and not VersionGrille.objects.exists()
    with pytest.raises(CommandError, match="En-tête"):
        call_command("publier_grille", _fichier(tmp_path, "a,b\n1,2\n"), par=admin.courriel)
    with pytest.raises(CommandError, match="entiers"):
        call_command(
            "publier_grille",
            _fichier(tmp_path, "pays,operateur,volume_min,prix_unitaire\nBF,ORANGE,0,19.5\n"),
            par=admin.courriel,
        )
