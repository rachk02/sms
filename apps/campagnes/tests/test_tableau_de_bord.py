"""Tableau de bord du client (T-92).

Les cinq indicateurs se lisent sur les compteurs pré-agrégés et sur la liste
noire du compte, jamais sur le journal des messages.
"""

import time
import uuid
from datetime import timedelta

import pytest
from django.db import connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.campagnes import selectors
from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts.models import ListeNoire
from apps.core.etats import Etat
from apps.journal import selectors as journal_lectures
from apps.journal.models import HORS_CAMPAGNE, Compteur, Evenement, Message
from apps.journal.services import changer_etat, enregistrer_message

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def compte(en_tant_que_worker):
    return creer_client(raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE)


@pytest.fixture
def membre(compte):
    return creer_utilisateur(
        courriel="awa@fasoriz.bf",
        mot_de_passe="x-1234-abcd",
        nom="Awa",
        client=compte,
        courriel_verifie_le=timezone.now(),
    )


def _ligne(client, heure, operateur="ORANGE", campagne_id=None, **valeurs):
    return Compteur(
        client=client,
        campagne_id=campagne_id or uuid.UUID(int=1),
        operateur=operateur,
        expediteur_libelle="FASORIZ",
        heure=heure,
        **valeurs,
    )


def test_les_cinq_indicateurs_sur_la_periode(compte):
    _, _, debut, fin = selectors.periode("7j")
    autre = creer_client(raison_sociale="Banque SA", forme=Client.Forme.SOCIETE)
    Compteur.objects.bulk_create(
        [
            _ligne(
                compte,
                fin - timedelta(hours=2),
                emis=60,
                livres=50,
                echecs=6,
                sans_accuse=4,
                latence_cumulee_ms=150_000,
                latences=50,
            ),
            _ligne(
                compte,
                fin - timedelta(hours=3),
                operateur="MOOV",
                emis=40,
                livres=30,
                echecs=10,
                refuses=3,
                latence_cumulee_ms=30_000,
                latences=30,
            ),
            # Hors de la période, et d'un autre client : ignorés.
            _ligne(compte, debut - timedelta(hours=1), emis=1_000, livres=1_000),
            _ligne(autre, fin - timedelta(hours=2), emis=500, livres=500),
        ]
    )
    ListeNoire.objects.create(
        client=compte, numero_normalise="+22670000001", origine=ListeNoire.Origine.STOP_SMS
    )
    ListeNoire.objects.create(
        client=compte, numero_normalise="+22670000002", origine=ListeNoire.Origine.MANUEL
    )

    tableau = selectors.tableau_de_bord(compte.id, "7j")
    assert not tableau.vide
    assert (tableau.emis, tableau.livres, tableau.refuses) == (100, 80, 3)
    # 80 livrés sur 100 messages au sort connu (50 + 6 + 4, 30 + 10).
    assert tableau.taux_livraison == 80.0
    # 180 secondes cumulées sur 80 livraisons.
    assert tableau.latence_moyenne_s == 2.2
    assert [(r.operateur, r.emis, r.part) for r in tableau.reseaux] == [
        ("ORANGE", 60, 60),
        ("MOOV", 40, 40),
    ]
    assert tableau.desinscriptions == 1


def test_une_periode_sans_donnees_affiche_un_etat_vide(client, membre):
    tableau = selectors.tableau_de_bord(membre.client_id, "24h")
    assert tableau.vide and tableau.taux_livraison is None and tableau.latence_moyenne_s is None
    client.force_login(membre)
    page = client.get("/tableau-de-bord", {"periode": "24h"}).content.decode()
    assert "Aucun envoi ni aucune désinscription sur cette période" in page
    assert "tableau-volume" not in page and "0 %" not in page


def test_le_fragment_met_a_jour_toutes_les_zones_en_une_requete(client, membre):
    Compteur.objects.create(
        client=membre.client,
        campagne_id=uuid.UUID(int=1),
        operateur="ORANGE",
        expediteur_libelle="FASORIZ",
        heure=timezone.now() - timedelta(hours=1),
        emis=3,
        livres=2,
        latence_cumulee_ms=4_000,
        latences=2,
    )
    client.force_login(membre)
    reponse = client.get("/tableau-de-bord/fragments/indicateurs", {"periode": "inconnue"})
    contenu = reponse.content.decode()
    assert reponse.status_code == 200
    assert 'id="tableau-zones" hx-swap-oob="true"' in contenu
    for zone in (
        "tableau-volume",
        "tableau-taux",
        "tableau-latence",
        "tableau-reseaux",
        "tableau-desinscriptions",
    ):
        assert f'id="{zone}"' in contenu
    # La sonde suivante garde la période, ramenée au défaut si elle est inconnue.
    assert "periode=7j" in contenu


def test_un_envoi_hors_campagne_compte_au_tableau_mais_pas_au_palier(compte):
    message = enregistrer_message(
        client=compte,
        numero_normalise="+22670123456",
        operateur="ORANGE",
        expediteur_libelle="FASORIZ",
        texte="Votre code : 1234",
        cout_unitaire=25,
        date_reception=timezone.now() - timedelta(minutes=5),
        nature=Message.Nature.CLIENT,
    )
    soumis_le = timezone.now() - timedelta(minutes=4)
    cle = {"id": message.id, "date_reception": message.date_reception}
    with transaction.atomic():
        changer_etat(
            Message.objects.select_for_update().get(**cle),
            Etat.SOUMIS,
            source=Evenement.Source.WORKER,
            horodatage=soumis_le,
            date_soumission=soumis_le,
        )
    with transaction.atomic():
        livre_le = soumis_le + timedelta(milliseconds=2_500)
        changer_etat(
            Message.objects.select_for_update().get(**cle),
            Etat.LIVRE,
            source=Evenement.Source.ACCUSE,
            horodatage=livre_le,
            date_accuse=livre_le,
        )
    lignes = Compteur.objects.filter(client=compte)
    assert set(lignes.values_list("campagne_id", flat=True)) == {HORS_CAMPAGNE}
    tableau = selectors.tableau_de_bord(compte.id, "24h")
    assert (tableau.emis, tableau.livres, tableau.latence_moyenne_s) == (1, 1, 2.5)
    # Le palier de volume des grilles ne compte que les campagnes, à trancher.
    assert journal_lectures.volume_mensuel(compte.id) == 0


def test_un_million_de_messages_se_lit_en_moins_d_une_seconde(client, membre):
    """Un an d'envois, heure par heure, sur trois réseaux : 26 280 lignes de
    compteurs pour 1 051 200 messages émis."""
    debut = timezone.now().replace(minute=0, second=0, microsecond=0)
    Compteur.objects.bulk_create(
        (
            _ligne(
                membre.client,
                debut - timedelta(hours=h),
                operateur=operateur,
                emis=40,
                livres=36,
                echecs=4,
                latence_cumulee_ms=36 * 3_000,
                latences=36,
            )
            for h in range(24 * 365)
            for operateur in ("ORANGE", "MOOV", "TELECEL")
        ),
        batch_size=5_000,
    )
    assert Compteur.objects.filter(client=membre.client).count() == 26_280
    client.force_login(membre)
    with (
        CaptureQueriesContext(connections["default"]) as requetes,
    ):
        depart = time.perf_counter()
        reponse = client.get("/tableau-de-bord", {"periode": "90j"})
        duree = time.perf_counter() - depart
    assert reponse.status_code == 200
    assert duree < 1.0
    assert not any("journal_message" in q["sql"] for q in requetes.captured_queries)
    plan = (
        Compteur.objects.filter(client=membre.client, heure__gte=debut - timedelta(days=90))
        .values("operateur")
        .explain()
    )
    assert "journal_message" not in plan
