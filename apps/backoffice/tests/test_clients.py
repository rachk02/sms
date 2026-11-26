"""Gestion des clients au back-office (T-95) : suspension, réactivation,
crédits offerts, fiche.

La suspension arrête les envois en cours et le dit avant : la page montre ce qui
sera arrêté et ne change rien tant que l'agent n'a pas confirmé avec un motif.
"""

import uuid
from datetime import timedelta

import pytest
from django.contrib.auth.models import Group
from django.test import override_settings
from django.utils import timezone

from apps.backoffice import roles
from apps.backoffice.services import (
    CreditRefuse,
    attribuer_credits,
    reactiver_compte,
    suspendre_compte,
)
from apps.campagnes import diffusion, services
from apps.campagnes.models import Campagne
from apps.campagnes.planification import liberer_programmees
from apps.campagnes.tests.test_pause import _en_file
from apps.comptes.models import Client
from apps.comptes.services import (
    MOTIF_COMPTE_NON_ACTIF,
    SuspensionRefusee,
    controle_compte_actif,
    creer_client,
    creer_utilisateur,
)
from apps.contacts import listes
from apps.contacts.services import creer_contact
from apps.core import controles
from apps.core.etats import Etat
from apps.core.models import Audit
from apps.credits import services as credits
from apps.credits.models import Ecriture
from apps.journal.models import Message
from apps.journal.services import enregistrer_message
from apps.telecom.models import Connecteur, Prefixe
from workers.envoi import WorkerEnvoi

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


def _preparer():
    Prefixe.objects.bulk_create(
        [Prefixe(pays="BF", prefixe="70", operateur="ORANGE")], ignore_conflicts=True
    )
    Connecteur.objects.create(
        nom=f"simule-{uuid.uuid4().hex[:6]}",
        operateur="ORANGE",
        type=Connecteur.Type.SIMULE,
        etat_liaison=Connecteur.EtatLiaison.CONNECTE,
        debit_max=100,
        parametres={"taux_echec": 0, "graine": 7},
    )
    client = creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    credits.acheter(client.id, 1_000_000, reference="OM-1")
    return client


@pytest.fixture
def compte(en_tant_que_worker):
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000"):
        yield _preparer()
    Prefixe.objects.filter(pays="BF").delete()


@pytest.fixture
def agent(compte):
    return creer_utilisateur(
        courriel="support@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Support",
        is_staff=True,
    )


def _campagne(client, nom, numeros, **programmation):
    liste = listes.creer(client=client, nom=f"Liste {nom}")
    listes.ajouter(
        liste, [creer_contact(client=client, numero=n, origine="SAISIE").id for n in numeros]
    )
    campagne = services.creer(
        client=client, auteur=None, nom=nom, texte="Promotion", listes=[liste.id], **programmation
    )
    vu = services.apercu(campagne)
    return services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )


def test_la_suspension_retient_les_envois_et_la_reactivation_ne_reprend_que_les_siens(
    compte, agent
):
    diffusee = _campagne(compte, "Promo", [f"70{n:06d}" for n in range(4)])
    diffusion.publier()
    file = _en_file(diffusee)
    retenue_par_le_client = _campagne(compte, "Rappel", ["70100000"])
    services.mettre_en_pause(retenue_par_le_client, auteur=None)

    pausees = suspendre_compte(compte, agent=agent, motif="Plainte ARCEP du 7 novembre")
    compte.refresh_from_db()
    assert compte.etat == Client.Etat.SUSPENDU
    assert [c.id for c in pausees] == [diffusee.id]
    trace = Audit.objects.get(action="comptes.suspension")
    assert trace.valeur_apres["campagnes_en_pause"] == [str(diffusee.id)]
    assert trace.motif == "Plainte ARCEP du 7 novembre" and trace.auteur_id == agent.id

    # Les messages en file de la campagne sont retenus, pas refusés.
    WorkerEnvoi(files=[])._soumettre(file[0])
    assert Message.objects.get(id=file[0]["message_id"]).etat == Etat.EN_ATTENTE
    # Un envoi hors campagne est refusé avant le réseau ; un message système part.
    unitaire = enregistrer_message(
        client=compte,
        numero_normalise="+22670999999",
        operateur="ORANGE",
        expediteur_libelle="FASORIZ",
        texte="Code 1234",
        cout_unitaire=20,
    )
    assert controles.refus(unitaire) == MOTIF_COMPTE_NON_ACTIF
    unitaire.nature = Message.Nature.SYSTEME
    assert controle_compte_actif(unitaire) is None
    # Le client ne reprend rien, ne confirme rien.
    with pytest.raises(services.CampagneRefusee, match="suspendu"):
        services.reprendre(diffusee, auteur=None)
    with pytest.raises(services.CampagneRefusee, match="suspendu"):
        _campagne(compte, "Nouvelle", ["70200000"])
    with pytest.raises(SuspensionRefusee):
        suspendre_compte(compte, agent=agent, motif="Encore")

    reprises = reactiver_compte(compte, agent=agent, motif="Plainte classée")
    assert [c.id for c in reprises] == [diffusee.id]
    assert Campagne.objects.get(id=diffusee.id).etat == Campagne.Etat.EN_COURS
    assert Campagne.objects.get(id=retenue_par_le_client.id).etat == Campagne.Etat.EN_PAUSE
    assert Audit.objects.filter(action="comptes.reactivation").count() == 1


def test_une_campagne_programmee_attend_la_reactivation(compte, agent):
    programmee = _campagne(
        compte, "Soldes", ["70000001"], planifiee_pour=timezone.now() + timedelta(hours=1)
    )
    assert programmee.etat == Campagne.Etat.PLANIFIEE
    suspendre_compte(compte, agent=agent, motif="Impayé")
    plus_tard = timezone.now() + timedelta(hours=2)
    assert liberer_programmees(plus_tard) == 0
    reactiver_compte(compte, agent=agent, motif="Réglé")
    assert liberer_programmees(plus_tard) == 1


def test_les_credits_offerts_portent_motif_auteur_et_trace(compte, agent):
    avant = credits.solde(compte.id).acquis
    with pytest.raises(CreditRefuse, match="motif"):
        attribuer_credits(compte, 5_000, agent=agent, motif="")
    with pytest.raises(CreditRefuse, match="entier"):
        attribuer_credits(compte, -5, agent=agent, motif="Erreur")
    ecriture = attribuer_credits(compte, 5_000, agent=agent, motif="Panne du 6 novembre")
    assert credits.solde(compte.id).acquis == avant + 5_000
    assert (ecriture.type, ecriture.motif, ecriture.auteur_id) == (
        Ecriture.Type.RECREDIT,
        "Panne du 6 novembre",
        agent.id,
    )
    trace = Audit.objects.get(action="credits.attribution")
    assert trace.valeur_apres["montant"] == 5_000 and trace.motif == "Panne du 6 novembre"


@pytest.fixture
def ecran(en_tant_que_backoffice):
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000"):
        client = _preparer()
        agents = {}
        for role in (roles.SUPPORT, roles.FINANCE):
            agents[role] = creer_utilisateur(
                courriel=f"{role.lower()}@el-immeka.test",
                mot_de_passe="une-phrase-solide",
                nom=role,
                is_staff=True,
            )
            agents[role].groups.add(Group.objects.get(name=role))
        yield client, agents
    Prefixe.objects.filter(pays="BF").delete()


def test_l_ecran_dit_ce_qui_sera_arrete_avant_de_confirmer(client, ecran):
    compte, agents = ecran
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000"):
        campagne = _campagne(compte, "Promo", ["70000001", "70000002"])
    adresse = f"/admin/comptes/client/{compte.id}/suspendre/"

    client.force_login(agents[roles.FINANCE])
    assert client.get(adresse).status_code == 403

    client.force_login(agents[roles.SUPPORT])
    fiche = client.get(f"/admin/comptes/client/{compte.id}/change/").content.decode()
    assert "Suspendre" in fiche and "Offrir des crédits" not in fiche
    page = client.get(adresse).content.decode()
    assert "Promo" in page and "2 message(s) pas encore partis" in page
    compte.refresh_from_db()
    assert compte.etat == Client.Etat.ACTIF
    assert not Audit.objects.filter(action="comptes.suspension").exists()

    assert client.post(adresse, {"motif": ""}).status_code == 200  # motif exigé
    reponse = client.post(adresse, {"motif": "Plainte ARCEP"})
    assert reponse.status_code == 302
    compte.refresh_from_db()
    assert compte.etat == Client.Etat.SUSPENDU
    assert Campagne.objects.get(id=campagne.id).etat == Campagne.Etat.EN_PAUSE
