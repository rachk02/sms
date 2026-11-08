"""Dictionnaire anti-spam (T-99).

Une expression bloquante refuse la campagne et se nomme ; un avertissement
prévient sans bloquer. La liste se modifie en base, sans redéploiement. Le
filtrage ne se contourne ni par des espaces ni par des substitutions de
caractères évidentes.
"""

import pytest
from django.contrib.auth.models import Group
from django.test import override_settings

from apps.backoffice import roles
from apps.backoffice.tests.test_clients import _preparer
from apps.campagnes import services
from apps.comptes.services import creer_utilisateur
from apps.conformite import services as conformite
from apps.conformite.antispam import normaliser
from apps.conformite.models import MotProscrit
from apps.core.models import Audit
from apps.telecom.models import Prefixe

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

Gravite = MotProscrit.Gravite


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ("Crédit gratuit", "creditgratuit"),
        ("C R É D I T   G R A T U I T", "creditgratuit"),
        ("c.r.e.d.i.t-gratuit", "creditgratuit"),
        ("CR3D1T GR4TU1T", "creditgratuit"),
        ("cr€€€dit gr@tuit", "creditgratuit"),
        ("Gagnez 1000 $", "gagnezios"),
    ],
)
def test_la_normalisation_defait_les_contournements_evidents(brut, attendu):
    assert normaliser(brut) == attendu


def _mot(expression, gravite=Gravite.BLOCAGE, motif=""):
    return conformite.enregistrer_mot_proscrit(
        MotProscrit(expression=expression, gravite=gravite, motif=motif), agent=None
    )


@pytest.fixture
def compte(en_tant_que_worker):
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000"):
        yield _preparer()
    Prefixe.objects.filter(pays="BF").delete()


@override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000")
def test_une_expression_bloquante_refuse_la_campagne_et_se_nomme(compte):
    _mot("crédit gratuit", motif="promesse trompeuse")
    for texte in (
        "Votre crédit gratuit vous attend",
        "Votre C R E D I T  gratuit vous attend",
        "Votre cr3d1t gr4tuuuit vous attend",
    ):
        with pytest.raises(services.CampagneRefusee, match="« crédit gratuit »") as refus:
            services.creer(client=compte, auteur=None, nom="Promo", texte=texte)
        assert "promesse trompeuse" in str(refus.value)
    # Un texte voisin passe.
    from apps.contacts import listes
    from apps.contacts.services import creer_contact

    liste = listes.creer(client=compte, nom="Clients")
    listes.ajouter(liste, [creer_contact(client=compte, numero="70000003", origine="SAISIE").id])
    assert services.creer(
        client=compte, auteur=None, nom="Ok", texte="Votre crédit est prêt", listes=[liste.id]
    )


@override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000")
def test_un_avertissement_previent_sans_bloquer(compte):
    from apps.contacts import listes
    from apps.contacts.services import creer_contact

    _mot("promo flash", gravite=Gravite.AVERTISSEMENT, motif="vocabulaire de spam")
    liste = listes.creer(client=compte, nom="Clients")
    listes.ajouter(liste, [creer_contact(client=compte, numero="70000001", origine="SAISIE").id])
    campagne = services.creer(
        client=compte, auteur=None, nom="Flash", texte="PROMO-FLASH ce soir", listes=[liste.id]
    )
    vu = services.apercu(campagne)
    assert [(c.expression, c.motif) for c in vu.avertissements] == [
        ("promo flash", "vocabulaire de spam")
    ]
    campagne = services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )
    assert campagne.etat == campagne.Etat.EN_ATTENTE


@override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000")
def test_une_expression_ajoutee_vaut_des_la_confirmation_suivante(compte):
    from apps.contacts import listes
    from apps.contacts.services import creer_contact

    liste = listes.creer(client=compte, nom="Clients")
    listes.ajouter(liste, [creer_contact(client=compte, numero="70000002", origine="SAISIE").id])
    campagne = services.creer(
        client=compte, auteur=None, nom="Soldes", texte="Gros lots a gagner", listes=[liste.id]
    )
    vu = services.apercu(campagne)
    assert vu.avertissements == []
    _mot("gros lot")
    with pytest.raises(services.CampagneRefusee, match="« gros lot »"):
        services.apercu(campagne)
    with pytest.raises(services.CampagneRefusee, match="« gros lot »"):
        services.confirmer(
            campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
        )
    # Retirée, la campagne repart.
    conformite.supprimer_mot_proscrit(MotProscrit.objects.get(), agent=None)
    services.confirmer(campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout)
    assert Audit.objects.filter(action="conformite.mot_proscrit.suppression").count() == 1


def test_une_expression_trop_courte_ou_en_double_est_refusee(en_tant_que_worker):
    with pytest.raises(conformite.MotRefuse, match="au moins"):
        _mot("a.b")
    _mot("arnaque")
    with pytest.raises(conformite.MotRefuse, match="déjà présent"):
        _mot("ARNAAAQUE")


def test_l_exploitation_tient_la_liste_au_back_office(client, en_tant_que_backoffice):
    agent = creer_utilisateur(
        courriel="exploitation@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Exploitation",
        is_staff=True,
    )
    agent.groups.add(Group.objects.get(name=roles.EXPLOITATION))
    client.force_login(agent)
    reponse = client.post(
        "/admin/conformite/motproscrit/add/",
        {"expression": "Gagnez une villa", "gravite": "BLOCAGE", "motif": "loterie", "actif": "on"},
    )
    assert reponse.status_code == 302
    mot = MotProscrit.objects.get()
    assert mot.forme == "gagnezunevia"
    trace = Audit.objects.get(action="conformite.mot_proscrit.creation")
    assert trace.auteur == agent and trace.valeur_apres["forme"] == "gagnezunevia"
    reponse = client.post(
        "/admin/conformite/motproscrit/add/",
        {"expression": "x y", "gravite": "BLOCAGE", "actif": "on"},
    )
    assert reponse.status_code == 200 and MotProscrit.objects.count() == 1
