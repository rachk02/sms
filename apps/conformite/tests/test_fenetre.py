"""Fenêtre légale et jours fériés, T-72 et T-73."""

import importlib
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest
from django.db import DatabaseError, transaction

from apps.comptes.services import creer_utilisateur
from apps.conformite import fenetre
from apps.conformite.models import Fenetre, JourFerie
from apps.core.models import Audit

pytestmark = pytest.mark.fenetre_reelle

OUAGA = ZoneInfo("Africa/Ouagadougou")
semis = importlib.import_module("apps.conformite.migrations.0003_fenetre_et_jours_feries")


def _a(jour: int, heure: int, minute: int = 0) -> datetime:
    """Une heure de la semaine du lundi 9 novembre 2026, à Ouagadougou."""
    return datetime(2026, 11, jour, heure, minute, tzinfo=OUAGA)


LUNDI, MARDI, VENDREDI, SAMEDI, DIMANCHE = 9, 10, 13, 14, 15


@pytest.fixture
def semaine(en_tant_que_worker):
    """La fenêtre semée par la migration : du lundi au samedi, de 8 h à 20 h."""
    for jour in semis.JOURS_OUVERTS:
        Fenetre.objects.get_or_create(
            pays="BF",
            jour=jour,
            defaults={"ouverture": semis.OUVERTURE, "fermeture": semis.FERMETURE},
        )


def test_la_migration_seme_la_fenetre_decidee():
    assert list(semis.JOURS_OUVERTS) == [1, 2, 3, 4, 5, 6]
    assert (semis.OUVERTURE, semis.FERMETURE) == (time(8, 0), time(20, 0))


@pytest.mark.parametrize(
    ("instant", "attendu"),
    [
        (_a(LUNDI, 7, 59), False),
        (_a(LUNDI, 8, 0), True),
        (_a(LUNDI, 12, 30), True),
        (_a(LUNDI, 19, 59), True),
        (_a(LUNDI, 20, 0), False),
        (_a(SAMEDI, 19, 0), True),
        (_a(DIMANCHE, 11, 0), False),
    ],
)
def test_la_fenetre_du_lundi_au_samedi_de_8_h_a_20_h(semaine, instant, attendu):
    assert fenetre.ouverte(instant) is attendu


def test_un_instant_en_temps_universel_est_lu_en_heure_locale(semaine):
    assert fenetre.ouverte(datetime(2026, 11, LUNDI, 9, 0, tzinfo=ZoneInfo("UTC")))
    # 9 h à Paris (heure d'hiver) font 8 h à Ouagadougou : déjà ouvert.
    assert fenetre.ouverte(datetime(2026, 11, LUNDI, 9, 0, tzinfo=ZoneInfo("Europe/Paris")))


def test_un_jour_ferie_ajoute_en_base_ferme_le_jour_sans_redemarrage(semaine):
    assert fenetre.ouverte(_a(LUNDI, 10))
    ferie = JourFerie.objects.create(pays="BF", date=date(2026, 11, LUNDI), libelle="Essai")
    assert not fenetre.ouverte(_a(LUNDI, 10))
    assert fenetre.ouverte(_a(MARDI, 10))
    ferie.delete()
    assert fenetre.ouverte(_a(LUNDI, 10))


@pytest.mark.parametrize(
    ("instant", "liberation"),
    [
        (_a(SAMEDI, 20, 30), _a(16, 8)),  # samedi soir : lundi 8 h
        (_a(DIMANCHE, 15), _a(16, 8)),
        (_a(VENDREDI, 6), _a(VENDREDI, 8)),  # avant l'ouverture : le jour même
        (_a(VENDREDI, 21), _a(SAMEDI, 8)),
    ],
)
def test_la_liberation_est_l_ouverture_suivante(semaine, instant, liberation):
    assert fenetre.prochaine_ouverture(instant) == liberation


def test_dans_la_fenetre_la_liberation_est_immediate(semaine):
    assert fenetre.prochaine_ouverture(_a(LUNDI, 10)) == _a(LUNDI, 10)


def test_les_jours_fermes_s_enchainent(semaine):
    JourFerie.objects.create(pays="BF", date=date(2026, 11, 16), libelle="Lundi férié")
    JourFerie.objects.create(pays="BF", date=date(2026, 11, 17), libelle="Mardi férié")
    assert fenetre.prochaine_ouverture(_a(SAMEDI, 21)) == _a(18, 8)


def test_sans_fenetre_configuree_aucune_heure_n_est_inventee(en_tant_que_worker):
    Fenetre.objects.all().delete()
    assert not fenetre.ouverte(_a(LUNDI, 10))
    assert fenetre.prochaine_ouverture(_a(LUNDI, 10)) is None


@pytest.mark.django_db
def test_le_portail_lit_la_fenetre_mais_n_y_ecrit_pas():
    Fenetre.objects.count()  # lisible sans client posé
    JourFerie.objects.count()
    with pytest.raises(DatabaseError), transaction.atomic():
        JourFerie.objects.create(pays="BF", date=date(2026, 12, 25), libelle="Noël")


@pytest.fixture
def agent(en_tant_que_backoffice):
    return creer_utilisateur(
        courriel="agent@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Agent",
        is_staff=True,
        is_superuser=True,
    )


def test_un_agent_saisit_un_jour_ferie_et_la_saisie_est_tracee(client, agent):
    client.force_login(agent)
    client.post(
        "/admin/conformite/jourferie/add/",
        {"pays": "BF", "date": "2026-12-25", "libelle": "Noël"},
    )
    jour = JourFerie.objects.get(date=date(2026, 12, 25))
    assert jour.libelle == "Noël"
    trace = Audit.objects.get(action="conformite.jour_ferie.enregistre")
    assert trace.auteur_id == agent.id and trace.valeur_apres["libelle"] == "Noël"

    client.post(f"/admin/conformite/jourferie/{jour.id}/delete/", {"post": "yes"})
    assert not JourFerie.objects.exists()
    assert Audit.objects.filter(action="conformite.jour_ferie.supprime").count() == 1


def test_un_agent_change_les_heures_et_le_changement_est_trace(client, agent):
    client.force_login(agent)
    lundi = Fenetre.objects.get_or_create(
        pays="BF", jour=1, defaults={"ouverture": time(8), "fermeture": time(20)}
    )[0]
    client.post(
        f"/admin/conformite/fenetre/{lundi.id}/change/",
        {"pays": "BF", "jour": "1", "ouverture": "09:00", "fermeture": "19:00"},
    )
    lundi.refresh_from_db()
    assert (lundi.ouverture, lundi.fermeture) == (time(9), time(19))
    trace = Audit.objects.get(action="conformite.fenetre.reglee")
    assert trace.valeur_avant["ouverture"] == "08:00"
    assert trace.valeur_apres["ouverture"] == "09:00"
