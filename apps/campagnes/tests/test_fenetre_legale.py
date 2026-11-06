# Les fixtures de test_pause sont importées : leurs noms reviennent en paramètres.
# ruff: noqa: F811

"""Plage horaire légale des campagnes marketing (T-72) et libération (T-74).

La fenêtre réelle, du lundi au samedi de 8 h à 20 h, est lue en base ; l'heure
est celle d'une horloge de test, que la fenêtre consulte à la place de
l'instant qu'on lui passe, pour que la publication et la retenue au worker
voient le même moment.
"""

from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import pytest

from apps.campagnes import diffusion, selectors, services
from apps.campagnes.models import Campagne
from apps.campagnes.tests.test_pause import (  # noqa: F401  (fixtures)
    PRIX,
    _confirmee,
    _en_file,
    _etats,
    client,
    reglages,
    worker,
)
from apps.conformite import fenetre
from apps.conformite.models import Fenetre, JourFerie
from apps.core.etats import Etat
from apps.credits import services as credits
from apps.credits.models import Reservation
from apps.journal.models import Message

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("reglages"),
    pytest.mark.fenetre_reelle,
]

OUAGA = ZoneInfo("Africa/Ouagadougou")
SAMEDI_SOIR = datetime(2026, 11, 14, 19, 58, tzinfo=OUAGA)
SAMEDI_FERME = datetime(2026, 11, 14, 20, 0, tzinfo=OUAGA)
DIMANCHE = datetime(2026, 11, 15, 11, 0, tzinfo=OUAGA)
LUNDI_OUVERTURE = datetime(2026, 11, 16, 8, 0, tzinfo=OUAGA)


@pytest.fixture
def horloge(client, monkeypatch):
    """La fenêtre du lundi au samedi, de 8 h à 20 h, et une horloge réglable."""
    for jour in range(1, 7):
        Fenetre.objects.get_or_create(
            pays="BF", jour=jour, defaults={"ouverture": time(8), "fermeture": time(20)}
        )
    vraie = fenetre.ouverte
    instant = {"maintenant": DIMANCHE}
    monkeypatch.setattr(
        fenetre, "ouverte", lambda _instant, pays="BF": vraie(instant["maintenant"], pays)
    )
    return instant


def _publier(horloge):
    return diffusion.publier(maintenant=horloge["maintenant"])


def test_une_campagne_marketing_confirmee_un_dimanche_attend_sans_etre_rejetee(client, horloge):
    campagne = _confirmee(client, 4)

    assert _publier(horloge) == 0
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.EN_ATTENTE and campagne.demarree_le is None
    assert Reservation.objects.get(campagne_id=campagne.id).etat == Reservation.Etat.ACTIVE
    assert selectors.liberation(campagne, DIMANCHE).prevue_le == LUNDI_OUVERTURE

    horloge["maintenant"] = LUNDI_OUVERTURE
    assert _publier(horloge) == 4
    campagne.refresh_from_db()
    assert campagne.demarree_le == LUNDI_OUVERTURE
    assert selectors.liberation(campagne, LUNDI_OUVERTURE) is None


def test_l_ecran_annonce_l_heure_de_liberation(client, horloge):
    campagne = _confirmee(client, 2)
    contexte = {"campagne": campagne, "liberation": selectors.liberation(campagne, DIMANCHE)}
    from django.template.loader import render_to_string

    page = render_to_string("campagnes/_suivi.html", contexte)
    assert "La diffusion reprendra seule le lundi 16 novembre 2026 à 08:00." in page


def test_les_messages_en_file_a_la_fermeture_attendent_puis_repartent(client, worker, horloge):
    horloge["maintenant"] = SAMEDI_SOIR
    campagne = _confirmee(client, 6)
    assert _publier(horloge) == 6
    file = _en_file(campagne)
    for donnees in file[:2]:
        worker._soumettre(donnees)

    horloge["maintenant"] = SAMEDI_FERME  # 20 h : la fenêtre se ferme
    for donnees in file[2:]:
        worker._soumettre(donnees)
    assert _etats(campagne) == {Etat.SOUMIS: 2, Etat.EN_ATTENTE: 4}

    # Toute la nuit et le dimanche, l'ordonnanceur ne remet rien en file et ne
    # clôt pas la campagne : sa réservation reste active.
    for instant in (SAMEDI_FERME, DIMANCHE):
        horloge["maintenant"] = instant
        assert _publier(horloge) == 0
    assert len(_en_file(campagne)) == 6
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.EN_COURS and campagne.terminee_le is None
    assert campagne.demarree_le == SAMEDI_SOIR  # le vrai départ n'est pas perdu
    assert Reservation.objects.get(campagne_id=campagne.id).etat == Reservation.Etat.ACTIVE
    assert selectors.liberation(campagne, DIMANCHE).prevue_le == LUNDI_OUVERTURE

    horloge["maintenant"] = LUNDI_OUVERTURE
    assert _publier(horloge) == 4  # les quatre retenus, remis en file
    for donnees in _en_file(campagne, depuis=6):
        worker._soumettre(donnees)
    assert _etats(campagne) == {Etat.SOUMIS: 6}
    _publier(horloge)  # clôture
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.TERMINEE
    assert campagne.cout_reel == 6 * PRIX


def test_une_campagne_transactionnelle_part_le_dimanche(client, worker, horloge):
    campagne = _confirmee(client, 3)
    Campagne.objects.filter(id=campagne.id).update(type=Campagne.Type.TRANSACTIONNEL)

    assert _publier(horloge) == 3
    for donnees in _en_file(campagne):
        worker._soumettre(donnees)
    assert _etats(campagne) == {Etat.SOUMIS: 3}
    assert selectors.liberation(Campagne.objects.get(id=campagne.id), DIMANCHE) is None


def test_un_envoi_unitaire_part_le_dimanche(client, worker, horloge):
    from apps.journal.services import enregistrer_message, mettre_en_file

    message = enregistrer_message(
        client=client,
        numero_normalise="+22670999999",
        operateur="ORANGE",
        expediteur_libelle="36000",
        texte="Votre code : 123456",
        cout_unitaire=PRIX,
    )
    credits.reserver(
        client.id,
        message.cout_total,
        message_id=message.id,
        message_date_reception=message.date_reception,
    )
    mettre_en_file(message, prioritaire=True)
    worker._soumettre(
        {"message_id": str(message.id), "date_reception": message.date_reception.isoformat()}
    )
    assert Message.objects.get(id=message.id).etat == Etat.SOUMIS


def test_un_jour_ferie_saisi_bloque_le_jour_sans_redemarrage(client, horloge):
    lundi = datetime(2026, 11, 16, 10, 0, tzinfo=OUAGA)
    horloge["maintenant"] = lundi
    JourFerie.objects.create(pays="BF", date=date(2026, 11, 16), libelle="Essai")
    campagne = _confirmee(client, 2)

    assert _publier(horloge) == 0
    mardi = datetime(2026, 11, 17, 8, 0, tzinfo=OUAGA)
    assert selectors.liberation(campagne, lundi).prevue_le == mardi


def test_annuler_une_campagne_en_attente_rend_ses_credits(client, horloge):
    avant = credits.solde(client.id).disponible
    campagne = _confirmee(client, 5)
    assert credits.solde(client.id).disponible == avant - 5 * PRIX
    assert _publier(horloge) == 0

    services.annuler(campagne, auteur=None)
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.ANNULEE and campagne.terminee_le
    assert Reservation.objects.get(campagne_id=campagne.id).etat == Reservation.Etat.LIBEREE
    assert credits.solde(client.id).disponible == avant
