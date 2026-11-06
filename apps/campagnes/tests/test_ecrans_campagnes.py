"""Écrans de la campagne : composition, aperçu, test et confirmation (T-61).

Le test lit la base par la connexion du worker : sous le rôle applicatif, sans
client posé, le cloisonnement lui masquerait tout."""

import statistics
import time

import pytest
from django.db import connection, connections
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.campagnes.models import Campagne, Destinataire
from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts.models import Contact, Liste, ListeContact
from apps.credits.models import CompteCredit, Ecriture
from apps.journal.models import Compteur, Message
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


def test_une_campagne_etalee_par_l_ecran(client, membre):
    client.force_login(membre)
    page = _creer_programmee(client, envoi="immediat", lissage="3")
    assert "Envoi étalé sur 180 minutes" in page
    assert "étalé sur 180 minutes</strong>" in page  # dans le texte de la modale
    campagne = Campagne.objects.using("worker").get()
    page = client.post(
        f"/campagnes/{campagne.id}/confirmer", {"destinataires": "3", "cout": "75"}, follow=True
    ).content.decode()
    assert "au plus 1 message(s) par minute" in page


# Suivi en direct (T-67) : le fragment ne lit que les compteurs et la réservation.

INTERDITES = ("journal_message", "journal_destinataire", "campagnes_destinataire")


def _confirmee_par_l_ecran(client, membre):
    client.force_login(membre)
    campagne, _ = _creer(client, membre)
    client.post(f"/campagnes/{campagne.id}/confirmer", {"destinataires": "3", "cout": "75"})
    return campagne


def _fragment(client, campagne):
    with CaptureQueriesContext(connection) as requetes:
        reponse = client.get(f"/campagnes/{campagne.id}/fragments/suivi")
    return reponse, [q["sql"] for q in requetes.captured_queries]


def test_le_fragment_met_a_jour_chaque_zone_sans_lire_le_journal(client, membre):
    campagne = _confirmee_par_l_ecran(client, membre)
    page = client.get(f"/campagnes/{campagne.id}").content.decode()
    assert 'id="suivi-sonde"' in page and "hx-swap-oob" not in page
    reponse, requetes = _fragment(client, campagne)
    fragment = reponse.content.decode()
    assert reponse.status_code == 200
    assert f'hx-get="/campagnes/{campagne.id}/fragments/suivi"' in fragment
    assert "every 2s" in fragment
    # Quatre zones échangées hors bande, la sonde seule remplacée à sa place.
    assert fragment.count('hx-swap-oob="true"') == 4
    assert not [sql for sql in requetes if any(table in sql for table in INTERDITES)]


def test_une_campagne_close_n_interroge_plus(client, membre):
    campagne = _confirmee_par_l_ecran(client, membre)
    client.post(f"/campagnes/{campagne.id}/annuler")
    fragment = _fragment(client, campagne)[0].content.decode()
    assert 'id="suivi-sonde"' in fragment and "hx-get" not in fragment
    assert "coût réel 0 FCFA" in fragment


def test_le_suivi_d_un_autre_compte_est_introuvable(client, membre):
    campagne = _confirmee_par_l_ecran(client, membre)
    autre = creer_utilisateur(
        courriel="v@autre.bf",
        mot_de_passe="x-1234-abcd",
        nom="V",
        client=creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE),
        role=Utilisateur.Role.ADMIN,
        courriel_verifie_le=timezone.now(),
    )
    client.force_login(autre)
    assert client.get(f"/campagnes/{campagne.id}/fragments/suivi").status_code == 404


def _duree(client, campagne) -> float:
    durees = []
    for _ in range(5):
        debut = time.perf_counter()
        client.get(f"/campagnes/{campagne.id}/fragments/suivi")
        durees.append(time.perf_counter() - debut)
    return statistics.median(durees)


def test_quarante_mille_messages_ne_ralentissent_pas_le_suivi(client, membre):
    campagne = _confirmee_par_l_ecran(client, membre)
    petit, requetes_petit = _fragment(client, campagne)
    duree_petit = _duree(client, campagne)
    # Quarante mille messages au journal, et leurs compteurs comme les écrivent les workers.
    with connections["worker"].cursor() as curseur:
        curseur.execute(
            """
            INSERT INTO journal_message (
                id, client_id, campagne_id, numero_normalise, operateur, expediteur_libelle,
                texte, encodage, segments, cout_unitaire, cout_total, etat, motif_echec,
                reference_smsc, cle_idempotence, date_reception, date_soumission, modifie_le
            )
            SELECT gen_random_uuid(), %s, %s, '+2267' || lpad(g::text, 7, '0'), 'ORANGE',
                '36000', 'Bonjour', 'GSM7', 1, 25, 25, 'LIVRE', '', '', '', now(), now(), now()
            FROM generate_series(1, 40000) AS g
            """,
            [campagne.client_id, campagne.id],
        )
        curseur.execute("ANALYZE journal_message")
    Campagne.objects.using("worker").filter(id=campagne.id).update(destinataires_prevus=40_003)
    Compteur.objects.using("worker").create(
        client_id=campagne.client_id,
        campagne_id=campagne.id,
        operateur="ORANGE",
        expediteur_libelle="36000",
        heure=timezone.now().replace(minute=0, second=0, microsecond=0),
        emis=40_000,
        livres=40_000,
        cout=1_000_000,
    )
    grand, requetes_grand = _fragment(client, campagne)
    assert "40000" in grand.content.decode() and "40000" not in petit.content.decode()
    assert len(requetes_grand) == len(requetes_petit)
    # Borne large : le temps de réponse ne suit pas le nombre de messages.
    assert _duree(client, campagne) < 3 * duree_petit + 0.05


def test_sans_grille_publiee_le_brouillon_explique_le_refus(client, membre):
    client.force_login(membre)
    campagne, _ = _creer(client, membre)
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=None):
        reponse = client.get(f"/campagnes/{campagne.id}")
    assert reponse.status_code == 200
    assert "Aucun tarif en vigueur pour le réseau ORANGE (BF)." in reponse.content.decode()
