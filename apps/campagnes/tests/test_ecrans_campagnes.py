"""Écrans de la campagne : composition, aperçu, test et confirmation (T-61).

Le test lit la base par la connexion du worker : sous le rôle applicatif, sans
client posé, le cloisonnement lui masquerait tout."""

import pytest
from django.test import override_settings
from django.utils import timezone

from apps.campagnes.models import Campagne, Destinataire
from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts.models import Contact, Liste, ListeContact
from apps.credits.models import CompteCredit, Ecriture
from apps.journal.models import Message
from apps.telecom.models import Prefixe

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture(autouse=True)
def reglages():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=25, SMS_NUMERO_COURT="36000"):
        yield


@pytest.fixture
def membre():
    compte = creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    base = Contact.objects.using("worker")
    Prefixe.objects.using("worker").create(pays="BF", prefixe="70", operateur="ORANGE")
    liste = Liste.objects.using("worker").create(
        client_id=compte.id, nom="Clients", nombre_contacts=3
    )
    for n in range(1, 4):
        contact = base.create(
            client_id=compte.id,
            numero_normalise=f"+2267000000{n}",
            numero_origine=str(n),
            prenom="Awa" if n == 1 else "",
            operateur_presume="ORANGE",
            origine="SAISIE",
        )
        ListeContact.objects.using("worker").create(
            liste=liste, contact_id=contact.id, client_id=compte.id, ajoute_le=timezone.now()
        )
    # Un achat de crédit, écrit comme le fait le service d'achat.
    CompteCredit.objects.using("worker").create(client_id=compte.id, solde_acquis=10_000)
    Ecriture.objects.using("worker").create(
        client_id=compte.id, type="ACHAT", montant=10_000, solde_apres=10_000, reference="OM-1"
    )
    utilisateur = creer_utilisateur(
        courriel="u@fasoriz.bf",
        mot_de_passe="x-1234-abcd",
        nom="U",
        client=compte,
        role=Utilisateur.Role.UTILISATEUR,
        courriel_verifie_le=timezone.now(),
    )
    yield utilisateur
    Prefixe.objects.using("worker").filter(pays="BF").delete()


def _creer(client, membre):
    liste = Liste.objects.using("worker").get()
    reponse = client.post(
        "/campagnes/nouvelle",
        {
            "nom": "Promo récolte",
            "type": "MARKETING",
            "texte": "Bonjour {prenom|cher client}, le riz est arrivé.",
            "listes": [str(liste.id)],
            "politique": "REPLI",
        },
    )
    return Campagne.objects.using("worker").get(), reponse


def test_la_confirmation_ecrit_destinataires_et_cout_en_toutes_lettres(client, membre):
    client.force_login(membre)
    campagne, reponse = _creer(client, membre)
    assert reponse["Location"] == f"/campagnes/{campagne.id}"
    page = client.get(f"/campagnes/{campagne.id}").content.decode()
    assert "trois destinataires (3)" in page
    assert "soixante-quinze francs CFA (75 FCFA)" in page
    assert "Envoyer à 3 destinataires pour 75 FCFA" in page  # le bouton nomme l'action
    assert "Bonjour cher client, le riz est arrivé." in page  # le cas le plus long


def test_confirmer_par_l_ecran_fige_et_reserve(client, membre):
    client.force_login(membre)
    campagne, _ = _creer(client, membre)
    page = client.post(
        f"/campagnes/{campagne.id}/confirmer", {"destinataires": "3", "cout": "75"}, follow=True
    ).content.decode()
    assert "confirmée" in page and "75 FCFA réservés" in page
    assert Destinataire.objects.using("worker").filter(campagne_id=campagne.id).count() == 3
    assert Campagne.objects.using("worker").get().etat == Campagne.Etat.EN_ATTENTE


def test_des_chiffres_perimes_sont_refuses_par_l_ecran(client, membre):
    client.force_login(membre)
    campagne, _ = _creer(client, membre)
    page = client.post(
        f"/campagnes/{campagne.id}/confirmer", {"destinataires": "2", "cout": "50"}, follow=True
    ).content.decode()
    assert "Les chiffres ont changé depuis votre lecture : 3 destinataires pour 75 FCFA" in page
    assert Campagne.objects.using("worker").get().etat == Campagne.Etat.BROUILLON


def test_l_envoi_de_test_par_l_ecran(client, membre):
    client.force_login(membre)
    campagne, _ = _creer(client, membre)
    page = client.post(
        f"/campagnes/{campagne.id}/test", {"numero": "70 99 88 77"}, follow=True
    ).content.decode()
    assert "Test envoyé à +22670998877 sous 36000, facturé 25 FCFA" in page
    assert Message.objects.using("worker").get().campagne_id is None


def test_une_campagne_sans_cible_est_expliquee(client, membre):
    client.force_login(membre)
    page = client.post(
        "/campagnes/nouvelle", {"nom": "Vide", "texte": "Bonjour", "politique": "REPLI"}
    ).content.decode()
    assert "Choisissez au moins une liste ou un segment" in page
    assert not Campagne.objects.using("worker").exists()


def test_pause_reprise_et_annulation_par_l_ecran(client, membre):
    client.force_login(membre)
    campagne, _ = _creer(client, membre)
    client.post(f"/campagnes/{campagne.id}/confirmer", {"destinataires": "3", "cout": "75"})
    page = client.post(f"/campagnes/{campagne.id}/pause", follow=True).content.decode()
    assert "en pause : plus aucun message ne part" in page
    assert "Reprendre la diffusion" in page and "Non soumis" in page
    # La modale d'annulation dit ce qui ne se rattrape pas, dans son propre texte.
    assert "0 message(s) déjà soumis au réseau ne peuvent pas être rattrapés" in page
    client.post(f"/campagnes/{campagne.id}/reprendre")
    assert Campagne.objects.using("worker").get().etat == Campagne.Etat.EN_ATTENTE
    page = client.post(f"/campagnes/{campagne.id}/annuler", follow=True).content.decode()
    assert "annulée : les messages non partis sont annulés" in page
    assert "coût réel 0 FCFA" in page
    assert CompteCredit.objects.using("worker").get().montant_reserve == 0


def test_une_campagne_d_un_autre_compte_ne_se_pilote_pas(client, membre):
    client.force_login(membre)
    campagne, _ = _creer(client, membre)
    client.post(f"/campagnes/{campagne.id}/confirmer", {"destinataires": "3", "cout": "75"})
    autre = creer_utilisateur(
        courriel="v@autre.bf",
        mot_de_passe="x-1234-abcd",
        nom="V",
        client=creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE),
        role=Utilisateur.Role.ADMIN,
        courriel_verifie_le=timezone.now(),
    )
    client.force_login(autre)
    assert client.post(f"/campagnes/{campagne.id}/annuler").status_code == 404
    assert Campagne.objects.using("worker").get().etat == Campagne.Etat.EN_ATTENTE


def _creer_programmee(client, **envoi):
    liste = Liste.objects.using("worker").get()
    return client.post(
        "/campagnes/nouvelle",
        {
            "nom": "Rappel",
            "type": "MARKETING",
            "texte": "Rappel de livraison.",
            "listes": [str(liste.id)],
            "politique": "REPLI",
            **envoi,
        },
        follow=True,
    ).content.decode()


def test_une_campagne_programmee_par_l_ecran(client, membre):
    client.force_login(membre)
    heure = timezone.localtime() + timezone.timedelta(days=2)
    page = _creer_programmee(
        client, envoi="programme", planifiee_pour=heure.strftime("%Y-%m-%dT%H:%M")
    )
    assert f"Envoi programmé le {heure:%d/%m/%Y à %H:%M}" in page
    assert "Programmer l'envoi à 3 destinataires pour 75 FCFA" in page
    passee = _creer_programmee(client, envoi="programme", planifiee_pour="2020-01-01T08:00")
    assert "programmée est déjà passée" in passee


def test_une_campagne_recurrente_par_l_ecran(client, membre):
    client.force_login(membre)
    page = _creer_programmee(client, envoi="recurrent", recurrence_jours="7", plafond="100")
    assert "Tous les 7 jour(s), plafond de 100 FCFA par envoi" in page
    assert "Programmer l'envoi récurrent, plafond 100 FCFA" in page
    sans_plafond = _creer_programmee(client, envoi="recurrent", recurrence_jours="7")
    assert "plafond de coût par envoi" in sans_plafond
