"""Détection de trafic artificiel (T-100).

Un envoi vers mille numéros consécutifs déclenche l'alerte. Chaque règle a ses
seuils en base. Une alerte prévient un humain sans rien bloquer ; au-delà du
seuil critique, l'envoi est bloqué jusqu'au traitement de l'alerte.
"""

import importlib
from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.test import override_settings
from django.utils import timezone

from apps.backoffice import roles
from apps.backoffice.tests.test_clients import _preparer
from apps.campagnes import services
from apps.comptes.services import creer_utilisateur
from apps.conformite import fraude
from apps.conformite import services as conformite
from apps.conformite.models import AlerteFraude, RegleFraude
from apps.contacts import listes
from apps.contacts.models import Contact
from apps.core import controles
from apps.core.etats import Etat
from apps.core.models import EvenementSortant
from apps.journal import fraude as fraude_journal
from apps.journal.services import enregistrer_message
from apps.telecom.models import Prefixe

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

Code = RegleFraude.Code
Gravite = AlerteFraude.Gravite


@pytest.fixture(autouse=True)
def regles(transactional_db):
    """Les seuils semés par la migration, reposés : le vidage de la base entre
    deux tests efface les lignes semées."""
    migration = importlib.import_module("apps.conformite.migrations.0005_fraude")
    for regle in migration.REGLES:
        RegleFraude.objects.using("worker").update_or_create(code=regle["code"], defaults=regle)


@pytest.fixture
def compte(en_tant_que_worker):
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000"):
        yield _preparer()
    Prefixe.objects.filter(pays="BF").delete()


def _regler(code, **seuils):
    regle = RegleFraude.objects.get(code=code)
    for champ, valeur in seuils.items():
        setattr(regle, champ, valeur)
    return conformite.regler_fraude(regle, agent=None)


def test_les_seuils_sont_en_base():
    assert set(RegleFraude.objects.values_list("code", flat=True)) == set(Code.values)
    assert RegleFraude.objects.get(code=Code.CONTIGUS).seuil_alerte == 1000


def _campagne_contigue(client, depart: int, nombre: int):
    contacts = Contact.objects.bulk_create(
        Contact(
            client=client,
            numero_normalise=f"+226{depart + rang}",
            operateur_presume="ORANGE",
            origine=Contact.Origine.SAISIE,
        )
        for rang in range(nombre)
    )
    liste = listes.creer(client=client, nom=f"Série {depart}")
    listes.ajouter(liste, [c.id for c in contacts])
    return services.creer(
        client=client, auteur=None, nom=f"Série {depart}", texte="Promotion", listes=[liste.id]
    )


def _confirmer(campagne):
    vu = services.apercu(campagne)
    return services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )


def test_mille_numeros_consecutifs_alertent_sans_bloquer(compte):
    campagne = _confirmer(_campagne_contigue(compte, 70_000_000, 1000))
    assert campagne.etat == campagne.Etat.EN_ATTENTE
    alerte = AlerteFraude.objects.get(regle=Code.CONTIGUS)
    assert (alerte.gravite, alerte.valeur, alerte.campagne_id) == (
        Gravite.ALERTE,
        1000,
        campagne.id,
    )
    assert alerte.details["premier"] == "+22670000000"
    assert alerte.details["dernier"] == "+22670000999"
    assert EvenementSortant.objects.filter(
        nom="conformite.fraude.alerte", objet_id=alerte.id
    ).exists()


def test_neuf_cent_quatre_vingt_dix_neuf_numeros_ne_disent_rien(compte):
    _confirmer(_campagne_contigue(compte, 70_100_000, 999))
    assert not AlerteFraude.objects.exists()


def test_au_dela_du_seuil_critique_la_campagne_est_refusee_et_l_alerte_reste(compte):
    _regler(Code.CONTIGUS, seuil_alerte=100, seuil_critique=500)
    campagne = _campagne_contigue(compte, 70_200_000, 600)
    with pytest.raises(services.CampagneRefusee, match="600 numéros consécutifs"):
        _confirmer(campagne)
    campagne.refresh_from_db()
    assert campagne.etat == campagne.Etat.BROUILLON
    alerte = AlerteFraude.objects.get(regle=Code.CONTIGUS)
    assert alerte.gravite == Gravite.CRITIQUE


def _code(client, numero, *, il_y_a=timedelta(0)):
    return enregistrer_message(
        client=client,
        numero_normalise=numero,
        operateur="ORANGE",
        expediteur_libelle="FASORIZ",
        texte="Votre code de connexion : 482193",
        cout_unitaire=20,
        date_reception=timezone.now() - il_y_a,
    )


def test_un_pic_de_codes_vers_une_plage_inhabituelle_alerte_puis_bloque(compte):
    _regler(Code.CODES_PLAGE, seuil_alerte=20, seuil_critique=30)
    for rang in range(19):
        _code(compte, f"+2267055{rang:04d}")
    assert not AlerteFraude.objects.exists()
    dernier = _code(compte, "+22670550019")
    alerte = AlerteFraude.objects.get(regle=Code.CODES_PLAGE)
    assert (alerte.gravite, alerte.plage, alerte.valeur) == (Gravite.ALERTE, "+2267055", 20)
    # Une alerte ne bloque rien.
    assert controles.refus(dernier) != fraude.MOTIF_PLAGE_BLOQUEE

    for rang in range(20, 30):
        dernier = _code(compte, f"+2267055{rang:04d}")
    alerte.refresh_from_db()
    assert alerte.gravite == Gravite.CRITIQUE and AlerteFraude.objects.count() == 1
    assert fraude.controle_plage_bloquee(dernier) == fraude.MOTIF_PLAGE_BLOQUEE
    # Une autre plage du même client part.
    assert fraude.controle_plage_bloquee(_code(compte, "+22670560001")) is None

    agent = creer_utilisateur(
        courriel="exploitation@el-immeka.test", mot_de_passe="une-phrase-solide", nom="E"
    )
    conformite.traiter_alerte(alerte, agent=agent, traitement="Client joint : test de charge")
    assert fraude.controle_plage_bloquee(dernier) is None


def test_une_plage_deja_servie_n_est_pas_inhabituelle(compte):
    _regler(Code.CODES_PLAGE, seuil_alerte=5, seuil_critique=50)
    _code(compte, "+22670570001", il_y_a=timedelta(minutes=90))
    for rang in range(10):
        _code(compte, f"+2267057{rang:04d}")
    assert not AlerteFraude.objects.exists()


def test_un_taux_de_livraison_bas_sur_une_plage_alerte(compte):
    _regler(Code.LIVRAISON_PLAGE, seuil_alerte=50, seuil_critique=20, echantillon_min=50)
    message = _code(compte, "+22670580001")
    maintenant = timezone.now()
    for rang in range(49):
        fraude_journal.noter_issue(message, Etat.LIVRE if rang < 15 else Etat.NON_LIVRE, maintenant)
    assert not AlerteFraude.objects.exists()  # échantillon trop petit
    fraude_journal.noter_issue(message, Etat.NON_LIVRE, maintenant)
    alerte = AlerteFraude.objects.get(regle=Code.LIVRAISON_PLAGE)
    assert (alerte.gravite, alerte.valeur) == (Gravite.ALERTE, 30)
    for _ in range(50):
        fraude_journal.noter_issue(message, Etat.EXPIRE, maintenant)
    alerte.refresh_from_db()
    assert (alerte.gravite, alerte.valeur) == (Gravite.CRITIQUE, 15)


def test_l_exploitation_traite_une_alerte_au_back_office(client, en_tant_que_backoffice):
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000"):
        compte = _preparer()
    alerte = fraude.signaler(
        compte.id, Code.CODES_PLAGE, gravite=Gravite.CRITIQUE, valeur=120, plage="+2267059"
    )
    support = creer_utilisateur(
        courriel="support@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Support",
        is_staff=True,
    )
    support.groups.add(Group.objects.get(name=roles.SUPPORT))
    exploitation = creer_utilisateur(
        courriel="exploitation@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Exploitation",
        is_staff=True,
    )
    exploitation.groups.add(Group.objects.get(name=roles.EXPLOITATION))
    adresse = f"/admin/conformite/alertefraude/{alerte.id}/traiter/"

    client.force_login(support)
    assert client.get(adresse).status_code == 403
    client.force_login(exploitation)
    assert "bloqués jusqu" in client.get(adresse).content.decode()
    assert client.post(adresse, {"traitement": ""}).status_code == 200
    assert client.post(adresse, {"traitement": "Numéros vérifiés"}).status_code == 302
    alerte.refresh_from_db()
    assert alerte.traitee_par == exploitation and alerte.traitement == "Numéros vérifiés"
    Prefixe.objects.filter(pays="BF").delete()
