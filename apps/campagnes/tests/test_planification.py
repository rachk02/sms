"""Envois programmés et récurrents (T-65).

Sous le rôle du worker, comme l'ordonnanceur. L'horloge est passée aux
fonctions de planification : aucun test n'attend.
"""

import threading
from datetime import timedelta

import pytest
from django.db import connection
from django.test import override_settings
from django.utils import timezone

from apps.campagnes import diffusion, planification, services
from apps.campagnes.models import Campagne, Destinataire
from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts import listes
from apps.contacts.services import creer_contact
from apps.core.models import EvenementSortant
from apps.credits import services as credits
from apps.credits.models import Reservation
from apps.journal.models import Message
from apps.telecom.models import Prefixe
from workers.ordonnanceur import CLE_VERROU, prendre_verrou

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("reglages"),
]

PRIX = 20
ACHAT = 1_000_000
JOUR = timedelta(days=1)


@pytest.fixture
def reglages():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=PRIX, SMS_NUMERO_COURT="36000"):
        yield


@pytest.fixture
def client(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        [Prefixe(pays="BF", prefixe="70", operateur="ORANGE")], ignore_conflicts=True
    )
    client = creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    credits.acheter(client.id, ACHAT, reference="OM-1")
    yield client
    Prefixe.objects.filter(pays="BF").delete()


def _liste(client, nombre: int, debut: int = 0):
    liste = listes.creer(client=client, nom=f"Liste {debut}")
    _ajouter(liste, client, nombre, debut)
    return liste


def _ajouter(liste, client, nombre: int, debut: int):
    listes.ajouter(
        liste,
        [
            creer_contact(client=client, numero=f"70{n:06d}", origine="SAISIE").id
            for n in range(debut, debut + nombre)
        ],
    )


def _creer(client, liste, **programmation) -> Campagne:
    return services.creer(
        client=client,
        auteur=None,
        nom="Promo",
        texte="Promotion",
        listes=[liste.id],
        **programmation,
    )


def _confirmer(campagne) -> Campagne:
    vu = services.apercu(campagne)
    return services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )


def _recurrente(client, liste, *, plafond=10_000, jours=7, debut=None, fin=None) -> Campagne:
    debut = debut or timezone.now() + timedelta(hours=1)
    campagne = _creer(
        client,
        liste,
        planifiee_pour=debut,
        recurrence_jours=jours,
        recurrence_fin=fin,
        plafond=plafond,
    )
    return _confirmer(campagne)


# Envoi programmé


def test_une_campagne_programmee_dans_le_passe_est_refusee_a_l_enregistrement(client):
    liste = _liste(client, 2)
    with pytest.raises(services.CampagneRefusee, match="déjà passée"):
        _creer(client, liste, planifiee_pour=timezone.now() - timedelta(minutes=1))
    assert not Campagne.objects.exists()


def test_une_heure_depassee_avant_la_confirmation_est_refusee(client):
    campagne = _creer(client, _liste(client, 2), planifiee_pour=timezone.now() + JOUR)
    Campagne.objects.filter(id=campagne.id).update(planifiee_pour=timezone.now() - JOUR)
    campagne.refresh_from_db()
    with pytest.raises(services.CampagneRefusee, match="passée"):
        _confirmer(campagne)
    assert not Reservation.objects.exists()


def test_une_campagne_programmee_est_reservee_puis_part_a_son_heure(client):
    heure = timezone.now() + timedelta(hours=2)
    campagne = _confirmer(_creer(client, _liste(client, 3), planifiee_pour=heure))
    assert campagne.etat == Campagne.Etat.PLANIFIEE
    assert Reservation.objects.get(campagne_id=campagne.id).montant_initial == 3 * PRIX
    assert planification.planifier(heure - timedelta(minutes=1)) == 0
    assert diffusion.publier() == 0  # pas avant son heure
    assert planification.planifier(heure) == 1
    assert Campagne.objects.get(id=campagne.id).etat == Campagne.Etat.EN_ATTENTE
    assert diffusion.publier() == 3


def test_une_campagne_programmee_annulee_avant_son_heure_rend_sa_reservation(client):
    heure = timezone.now() + timedelta(hours=2)
    campagne = _confirmer(_creer(client, _liste(client, 3), planifiee_pour=heure))
    services.annuler(campagne, auteur=None)
    assert credits.solde(client.id).disponible == ACHAT
    assert planification.planifier(heure) == 0
    assert diffusion.publier() == 0


# Récurrence


def test_la_confirmation_d_une_recurrence_ne_fige_ni_ne_reserve_rien(client):
    patron = _recurrente(client, _liste(client, 3))
    assert patron.etat == Campagne.Etat.PLANIFIEE and patron.recurrente
    assert patron.prochaine_occurrence == patron.planifiee_pour
    assert not Destinataire.objects.filter(campagne=patron).exists()
    assert not Reservation.objects.exists()


def test_une_occurrence_sous_le_plafond_part_seule_une_fois_par_creneau(client):
    patron = _recurrente(client, _liste(client, 3))
    heure = patron.prochaine_occurrence
    assert planification.planifier(heure) == 1
    assert planification.planifier(heure + timedelta(minutes=5)) == 0  # rejoué : rien de plus
    occurrence = Campagne.objects.get(parent=patron)
    assert occurrence.etat == Campagne.Etat.EN_ATTENTE
    assert occurrence.occurrence_le == heure
    assert Reservation.objects.get(campagne_id=occurrence.id).montant_initial == 3 * PRIX
    patron.refresh_from_db()
    assert patron.prochaine_occurrence == heure + timedelta(days=7)
    assert diffusion.publier() == 3


def test_le_segment_est_recalcule_a_chaque_occurrence(client):
    liste = _liste(client, 2)
    patron = _recurrente(client, liste, jours=1)
    premiere = patron.prochaine_occurrence
    planification.planifier(premiere)
    _ajouter(liste, client, 3, debut=10)
    planification.planifier(premiere + JOUR)
    prevus = list(
        Campagne.objects.filter(parent=patron)
        .order_by("occurrence_le")
        .values_list("destinataires_prevus", flat=True)
    )
    assert prevus == [2, 5]


def test_une_occurrence_au_dessus_du_plafond_attend_et_le_signale(client):
    patron = _recurrente(client, _liste(client, 5), plafond=4 * PRIX)
    planification.planifier(patron.prochaine_occurrence)
    occurrence = Campagne.objects.get(parent=patron)
    assert occurrence.etat == Campagne.Etat.BROUILLON
    assert not Reservation.objects.exists()
    signal = EvenementSortant.objects.get(nom="campagne.occurrence.a_confirmer")
    assert signal.objet_id == occurrence.id and "plafond" in signal.donnees["motif"]
    # Le client confirme lui-même, avec les chiffres qu'il lit.
    occurrence = _confirmer(occurrence)
    assert occurrence.etat == Campagne.Etat.EN_ATTENTE


def test_un_solde_insuffisant_laisse_l_occurrence_en_attente(client):
    patron = _recurrente(client, _liste(client, 3))
    # Il ne reste que de quoi servir un destinataire sur trois.
    credits.reserver(client.id, ACHAT - PRIX, campagne_id=patron.id)
    planification.planifier(patron.prochaine_occurrence)
    occurrence = Campagne.objects.get(parent=patron)
    assert occurrence.etat == Campagne.Etat.BROUILLON
    motif = EvenementSortant.objects.get(nom="campagne.occurrence.a_confirmer").donnees["motif"]
    assert "Solde insuffisant" in motif
    assert not Destinataire.objects.filter(campagne=occurrence).exists()


def test_apres_un_arret_une_seule_occurrence_part_pour_le_dernier_creneau(client):
    patron = _recurrente(client, _liste(client, 2), jours=7)
    premiere = patron.prochaine_occurrence
    trois_semaines_plus_tard = premiere + timedelta(days=20)
    assert planification.planifier(trois_semaines_plus_tard) == 1
    occurrence = Campagne.objects.get(parent=patron)
    assert occurrence.occurrence_le == premiere + timedelta(days=14)
    patron.refresh_from_db()
    assert patron.prochaine_occurrence == premiere + timedelta(days=21)


def test_la_recurrence_s_arrete_a_sa_date_de_fin(client):
    debut = timezone.now() + timedelta(hours=1)
    patron = _recurrente(client, _liste(client, 2), jours=1, debut=debut, fin=debut + JOUR)
    planification.planifier(debut)
    planification.planifier(debut + JOUR)
    patron.refresh_from_db()
    assert patron.etat == Campagne.Etat.TERMINEE
    assert planification.planifier(debut + 3 * JOUR) == 0
    assert Campagne.objects.filter(parent=patron).count() == 2


def test_arreter_une_recurrence_arrete_les_occurrences_suivantes(client):
    patron = _recurrente(client, _liste(client, 2), jours=1)
    planification.planifier(patron.prochaine_occurrence)
    services.annuler(patron, auteur=None)
    patron.refresh_from_db()
    assert patron.etat == Campagne.Etat.ANNULEE and patron.terminee_le
    assert planification.planifier(patron.prochaine_occurrence + 5 * JOUR) == 0
    occurrence = Campagne.objects.get(parent=patron)
    assert occurrence.etat == Campagne.Etat.EN_ATTENTE  # garde son propre cycle


def test_une_recurrence_en_echec_n_arrete_pas_les_autres(client, monkeypatch):
    casse = _recurrente(client, _liste(client, 2, debut=0))
    saine = _recurrente(client, _liste(client, 2, debut=100))
    original = services.creer_occurrence

    def creer_occurrence(patron, creneau):
        if patron.id == casse.id:
            raise RuntimeError("panne simulée")
        return original(patron, creneau)

    monkeypatch.setattr(services, "creer_occurrence", creer_occurrence)
    heure = max(casse.prochaine_occurrence, saine.prochaine_occurrence)
    assert planification.planifier(heure) == 1
    assert Campagne.objects.filter(parent=saine).count() == 1
    assert not Campagne.objects.filter(parent=casse).exists()


def test_validation_de_la_recurrence(client):
    liste = _liste(client, 1)
    heure = timezone.now() + JOUR
    with pytest.raises(services.CampagneRefusee, match="plafond"):
        _creer(client, liste, planifiee_pour=heure, recurrence_jours=7)
    with pytest.raises(services.CampagneRefusee, match="Fréquence"):
        _creer(client, liste, planifiee_pour=heure, recurrence_jours=3, plafond=100)
    with pytest.raises(services.CampagneRefusee, match="fin"):
        _creer(
            client,
            liste,
            planifiee_pour=heure,
            recurrence_jours=7,
            plafond=100,
            recurrence_fin=heure - JOUR,
        )


# Deux ordonnanceurs


def test_deux_ordonnanceurs_lances_en_meme_temps_ne_declenchent_qu_un_envoi(client):
    """Deux sessions PostgreSQL réelles, une par fil : seul celui qui prend le
    verrou consultatif planifie et publie."""
    patron = _recurrente(client, _liste(client, 4))
    heure = patron.prochaine_occurrence
    depart, fini = threading.Barrier(2), threading.Barrier(2)
    actifs, erreurs = [], []

    def ordonnanceur(nom):
        try:
            depart.wait(10)
            if prendre_verrou():
                actifs.append(nom)
                planification.planifier(heure)
                diffusion.publier()
            fini.wait(10)  # le verrou reste tenu jusqu'à ce que l'autre ait essayé
        except Exception as erreur:
            erreurs.append(erreur)
        finally:
            with connection.cursor() as curseur:
                curseur.execute("SELECT pg_advisory_unlock_all()")
            connection.close()

    fils = [threading.Thread(target=ordonnanceur, args=(n,)) for n in ("a", "b")]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(30)
    assert not erreurs
    assert len(actifs) == 1
    occurrence = Campagne.objects.get(parent=patron)
    messages = Message.objects.filter(campagne_id=occurrence.id)
    assert messages.count() == 4
    assert len(set(messages.values_list("contact_id", flat=True))) == 4
    assert prendre_verrou()  # libéré à la fin des deux sessions
    with connection.cursor() as curseur:
        curseur.execute("SELECT pg_advisory_unlock(%s)", [CLE_VERROU])


def test_sans_verrou_deux_tours_concurrents_ne_creent_qu_une_occurrence(client):
    """La contrainte d'unicité tient même si le verrou venait à manquer."""
    patron = _recurrente(client, _liste(client, 2))
    heure = patron.prochaine_occurrence
    depart, erreurs = threading.Barrier(2), []

    def tour():
        try:
            depart.wait(10)
            planification.declencher_occurrence(patron.id, heure)
        except Exception as erreur:
            erreurs.append(erreur)
        finally:
            connection.close()

    fils = [threading.Thread(target=tour) for _ in range(2)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(30)
    assert not erreurs
    assert Campagne.objects.filter(parent=patron).count() == 1
    assert Reservation.objects.count() == 1
