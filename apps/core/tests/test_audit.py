"""Journal d'audit, T-41."""

import pytest
from django.db import ProgrammingError, connections, transaction

from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.audit import auditer
from apps.core.models import Audit, AuditEcritureSeuleError


@pytest.fixture
def agent(transactional_db):
    return creer_utilisateur(
        courriel="agent@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Agent",
        is_staff=True,
        is_superuser=True,
    )


@pytest.fixture
def entree(agent):
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    utilisateur = creer_utilisateur(
        courriel="u@exemple.bf", mot_de_passe="x-1234-abcd", nom="U", client=client
    )
    return auditer(
        "comptes.role.modifie",
        utilisateur,
        auteur=agent,
        client=client,
        avant={"role": "UTILISATEUR"},
        apres={"role": "ADMIN"},
        motif="Nouvelle responsable des campagnes",
        adresse_ip="10.0.0.5",
    )


def _lire(using="backoffice"):
    return Audit.objects.using(using)


@pytest.mark.django_db(transaction=True, databases=["default", "backoffice"])
def test_une_action_laisse_une_ecriture_complete(entree, agent):
    lue = _lire().get(id=entree.id)
    assert (lue.action, lue.objet_type) == ("comptes.role.modifie", "comptes.utilisateur")
    assert (lue.valeur_avant, lue.valeur_apres) == ({"role": "UTILISATEUR"}, {"role": "ADMIN"})
    assert lue.auteur_id == agent.id and lue.motif and lue.horodatage
    assert lue.adresse_ip == "10.0.0.5"


@pytest.mark.django_db(transaction=True, databases=["default", "backoffice"])
def test_aucune_methode_ne_modifie_ni_ne_supprime_une_entree(entree):
    lue = _lire().get(id=entree.id)
    lue.motif = "réécrit"
    with pytest.raises(AuditEcritureSeuleError):
        lue.save()
    with pytest.raises(AuditEcritureSeuleError):
        lue.delete()
    with pytest.raises(AuditEcritureSeuleError):
        _lire().filter(id=entree.id).update(motif="réécrit")
    with pytest.raises(AuditEcritureSeuleError):
        _lire().filter(id=entree.id).delete()


@pytest.mark.django_db(transaction=True, databases=["default", "backoffice"])
@pytest.mark.parametrize("sql", ["UPDATE core_audit SET motif = 'x'", "DELETE FROM core_audit"])
def test_meme_le_back_office_ne_peut_rien_modifier_en_sql(entree, sql):
    with (
        pytest.raises(ProgrammingError, match="écriture seule"),
        transaction.atomic(using="backoffice"),
    ):
        with connections["backoffice"].cursor() as curseur:
            curseur.execute(sql)
    assert _lire().get(id=entree.id).motif == "Nouvelle responsable des campagnes"


@pytest.mark.django_db(transaction=True, databases=["default", "backoffice"])
def test_le_role_applicatif_ecrit_au_journal_sans_pouvoir_le_lire(entree):
    assert not Audit.objects.exists()
    assert _lire().filter(id=entree.id).exists()


@pytest.mark.django_db(transaction=True, databases=["default", "backoffice"])
def test_le_journal_se_consulte_et_se_filtre_au_back_office(
    client, entree, agent, en_tant_que_backoffice
):
    client.force_login(Utilisateur.objects.get(id=agent.id))
    liste = client.get("/admin/core/audit/")
    # Le motif n'apparaît que dans les lignes de résultat, pas dans les filtres.
    assert liste.status_code == 200 and "Nouvelle responsable" in liste.content.decode()
    filtre = client.get("/admin/core/audit/?action=autre.action")
    assert "Nouvelle responsable" not in filtre.content.decode()
    retenu = client.get("/admin/core/audit/?action=comptes.role.modifie")
    assert "Nouvelle responsable" in retenu.content.decode()
    assert client.get("/admin/core/audit/add/").status_code == 403
    detail = client.post(f"/admin/core/audit/{entree.id}/change/", {"motif": "x"})
    assert detail.status_code in (403, 200)
    assert _lire().get(id=entree.id).motif == "Nouvelle responsable des campagnes"
