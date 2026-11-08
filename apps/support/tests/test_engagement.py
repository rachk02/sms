"""Heures ouvrées du compteur d'engagement (T-102)."""

from datetime import date, datetime

from django.utils import timezone

from apps.support.engagement import secondes_ouvrees


def _a(jour, heure, minute=0):
    return timezone.make_aware(datetime(2026, 11, jour, heure, minute))


def test_un_week_end_ne_compte_que_le_samedi_matin():
    # Vendredi 6 novembre 17 h au lundi 9 novembre 9 h : une heure le vendredi,
    # quatre le samedi matin, une le lundi.
    assert secondes_ouvrees(_a(6, 17), _a(9, 9)) == 6 * 3600


def test_la_p1_court_en_continu():
    assert secondes_ouvrees(_a(6, 17), _a(9, 9), continu=True) == 64 * 3600


def test_un_jour_ferie_ne_compte_pas():
    feries = frozenset({date(2026, 11, 9)})
    assert secondes_ouvrees(_a(6, 17), _a(9, 9), feries=feries) == 5 * 3600


def test_la_nuit_et_le_dimanche_ne_comptent_pas():
    assert secondes_ouvrees(_a(9, 18, 30), _a(10, 7)) == 0
    assert secondes_ouvrees(_a(8, 9), _a(8, 17)) == 0
    assert secondes_ouvrees(_a(10, 7), _a(10, 8, 30)) == 1800
