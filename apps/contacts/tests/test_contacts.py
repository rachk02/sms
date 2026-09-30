"""Contacts et classification par opérateur, T-43 et T-44."""

import json
import threading

import pytest
from django.db import connection, connections, transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts.models import Contact
from apps.contacts.services import ContactExistant, confirmer_operateur, creer_contact
from apps.core.cloisonnement import poser_client
from apps.core.numeros import NumeroInvalide
from apps.telecom.models import Prefixe
from apps.telecom.prefixes import FichierPrefixesInvalide, charger, classer

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

# Préfixes fictifs : les vrais viennent d'un fichier confirmé auprès de l'ARCEP.
PREFIXES_ESSAI = "prefixe,operateur\n70,ORANGE\n71,MOOV\n7155,TELECEL\n"


@pytest.fixture
def prefixes():
    with transaction.atomic(using="worker"):
        Prefixe.objects.using("worker").filter(pays="BF").delete()
        Prefixe.objects.using("worker").bulk_create(
            [
                Prefixe(pays="BF", prefixe=p, operateur=o)
                for p, o in (("70", "ORANGE"), ("71", "MOOV"), ("7155", "TELECEL"))
            ]
        )
    yield
    Prefixe.objects.using("worker").filter(pays="BF").delete()


@pytest.fixture
def client_pose():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    return client


def _creer(client, numero, **champs):
    with transaction.atomic():
        poser_client(client.id)
        return creer_contact(client=client, numero=numero, origine="SAISIE", **champs)


def test_aucun_prefixe_n_est_fourni_par_le_code():
    assert not Prefixe.objects.using("worker").exists()


def test_le_contact_est_normalise_et_son_operateur_presume(client_pose, prefixes):
    contact = _creer(client_pose, "70 12 34 56", nom=" Awa ")
    assert (contact.numero_normalise, contact.numero_origine) == ("+22670123456", "70 12 34 56")
    assert (contact.operateur_presume, contact.operateur_confirme, contact.nom) == (
        "ORANGE",
        "",
        "Awa",
    )


def test_le_plus_long_prefixe_l_emporte(prefixes):
    assert classer("+22671123456") == "MOOV"
    assert classer("+22671551234") == "TELECEL"
    assert classer("+22699123456") == ""


def test_une_tranche_ajoutee_en_base_change_le_classement_sans_redemarrage(prefixes):
    assert classer("+22668123456") == ""
    Prefixe.objects.using("worker").create(pays="BF", prefixe="68", operateur="TELECEL")
    assert classer("+22668123456") == "TELECEL"


def test_un_numero_invalide_est_refuse(client_pose):
    with pytest.raises(NumeroInvalide, match="trop court"):
        _creer(client_pose, "7012")


def test_deux_insertions_simultanees_du_meme_numero_donnent_une_ligne(client_pose, prefixes):
    """Deux transactions réelles, sur deux connexions, au même instant."""
    depart, resultats = threading.Barrier(2), []

    def inserer():
        try:
            depart.wait(5)
            _creer(client_pose, "+22670999999")
            resultats.append("cree")
        except ContactExistant:
            resultats.append("refuse")
        finally:
            connection.close()

    fils = [threading.Thread(target=inserer) for _ in range(2)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(10)

    assert sorted(resultats) == ["cree", "refuse"]
    with transaction.atomic():
        poser_client(client_pose.id)
        assert Contact.objects.filter(numero_normalise="+22670999999").count() == 1


def test_le_meme_numero_chez_deux_clients_est_permis(client_pose, prefixes):
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    _creer(client_pose, "70123456")
    _creer(autre, "70123456")


def test_un_accuse_livre_confirme_l_operateur(client_pose, prefixes):
    contact = _creer(client_pose, "71551234")
    assert contact.operateur_presume == "TELECEL"
    with transaction.atomic():
        poser_client(client_pose.id)
        assert confirmer_operateur(client_pose.id, contact.numero_normalise, "MOOV") == 1
        contact.refresh_from_db()
    assert (contact.operateur_confirme, contact.operateur) == ("MOOV", "MOOV")


def test_le_worker_applique_la_confirmation_une_seule_fois(client_pose, prefixes):
    from workers.accuses import _confirmer_operateur

    contact = _creer(client_pose, "70111222")

    class Message:
        message_id = f"livre:{timezone.now().timestamp()}"
        body = json.dumps(
            {
                "client_id": str(client_pose.id),
                "cle_idempotence": message_id,
                "donnees": {"numero_normalise": contact.numero_normalise, "operateur": "ORANGE"},
            }
        ).encode()

    reglages = connections["default"].settings_dict
    avant = reglages["USER"], reglages["PASSWORD"]
    connections["default"].close()
    reglages["USER"] = connections["worker"].settings_dict["USER"]
    reglages["PASSWORD"] = connections["worker"].settings_dict["PASSWORD"]
    try:
        _confirmer_operateur(Message)
        _confirmer_operateur(Message)
        assert Contact.objects.get(id=contact.id).operateur_confirme == "ORANGE"
    finally:
        connections["default"].close()
        reglages["USER"], reglages["PASSWORD"] = avant


def test_le_role_applicatif_lit_les_prefixes_sans_pouvoir_les_modifier(prefixes):
    from django.db import ProgrammingError

    assert Prefixe.objects.count() == 3
    with pytest.raises(ProgrammingError, match="row-level security"), transaction.atomic():
        Prefixe.objects.create(pays="BF", prefixe="99", operateur="ORANGE")


def test_le_chargement_d_un_fichier_remplace_la_table(prefixes):
    assert charger("prefixe,operateur\n# commentaire\n76,ORANGE\n", using="worker") == 1
    assert classer("+22676123456") == "ORANGE" and classer("+22670123456") == ""
    with pytest.raises(FichierPrefixesInvalide, match="Ligne 2"):
        charger("prefixe,operateur\n7x,ORANGE\n", using="worker")
