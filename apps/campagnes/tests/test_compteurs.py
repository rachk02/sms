"""Compteurs pré-agrégés : contrôle contre le journal et recalcul (T-91).

Une campagne terminée a les mêmes chiffres dans ses compteurs et dans le
journal, panne d'un worker et rejeux compris. Un écart se voit au contrôle et
se corrige par un recalcul qui remplace et n'ajoute pas.
"""

import threading
import uuid
from unittest import mock

import pytest
from django.core.management import CommandError, call_command
from django.db import connections
from django.db.models import F
from django.test import override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.campagnes import controle, diffusion, selectors, services
from apps.campagnes.tests.test_pause import _en_file
from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts import listes
from apps.contacts.services import creer_contact
from apps.credits import services as credits
from apps.journal import selectors as journal_lectures
from apps.journal.models import Compteur
from apps.journal.services import appliquer_accuse, recompter
from apps.telecom.models import Connecteur, Prefixe
from workers.envoi import WorkerEnvoi

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def compte(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        [
            Prefixe(pays="BF", prefixe="70", operateur="ORANGE"),
            Prefixe(pays="BF", prefixe="60", operateur="MOOV"),
        ],
        ignore_conflicts=True,
    )
    for operateur in ("ORANGE", "MOOV"):
        Connecteur.objects.create(
            nom=f"simule-{uuid.uuid4().hex[:6]}",
            operateur=operateur,
            type=Connecteur.Type.SIMULE,
            etat_liaison=Connecteur.EtatLiaison.CONNECTE,
            debit_max=100,
            parametres={"taux_echec": 0.5, "graine": 7},
        )
    client = creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    credits.acheter(client.id, 1_000_000, reference="OM-1")
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000"):
        yield client
    Prefixe.objects.filter(pays="BF").delete()


def _terminee(client, garder: int = 0):
    """Une campagne envoyée jusqu'au bout, avec une panne pendant les accusés.
    Les `garder` derniers accusés ne sont pas appliqués : ils sont rendus."""
    numeros = [f"70{n:06d}" for n in range(6)] + [f"60{n:06d}" for n in range(4)]
    liste = listes.creer(client=client, nom="Clients")
    listes.ajouter(
        liste, [creer_contact(client=client, numero=n, origine="SAISIE").id for n in numeros]
    )
    campagne = services.creer(
        client=client, auteur=None, nom="Promo", texte="Promotion", listes=[liste.id]
    )
    vu = services.apercu(campagne)
    campagne = services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )
    diffusion.publier()
    worker = WorkerEnvoi(files=[])
    file = _en_file(campagne)
    for donnees in file + file:  # la file relivre après une reprise
        worker._soumettre(donnees)
    accuses = [
        accuse
        for passerelle in worker.passerelles.values()
        for accuse in passerelle.accuses_dus(timezone.now() + timezone.timedelta(hours=1))
    ]
    # Le worker tombe au milieu de l'écriture d'un accusé : l'état et le
    # compteur s'annulent ensemble. À la reprise, tout est rejoué.
    with mock.patch("apps.journal.services._compter", side_effect=RuntimeError("panne")):
        with pytest.raises(RuntimeError):
            appliquer_accuse(accuses[0])
    appliques = accuses[: len(accuses) - garder]
    for accuse in appliques + appliques:
        appliquer_accuse(accuse)
    diffusion.publier()
    campagne.refresh_from_db()
    assert campagne.terminee_le
    if garder:
        return campagne, accuses[len(accuses) - garder :]
    return campagne


def _lignes(campagne):
    return sorted(
        Compteur.objects.filter(campagne_id=campagne.id).values_list(
            "operateur", "expediteur_libelle", "heure", "emis", "livres", "echecs", "cout"
        )
    )


def test_le_compteur_d_une_campagne_terminee_egale_le_journal(compte):
    campagne = _terminee(compte)
    reel = journal_lectures.compte_journal(
        compte.id, campagne.id, campagne.confirmee_le, campagne.terminee_le
    )
    assert reel["emis"] == 10 and reel["livres"] + reel["echecs"] == 10
    assert reel["cout"] == 10 * 20
    assert journal_lectures.compte_compteurs(compte.id, campagne.id) == reel
    assert controle.controler(campagne) is None
    assert controle.controler_terminees() == []


def test_un_ecart_se_signale_et_le_recalcul_ne_compte_pas_deux_fois(compte):
    campagne = _terminee(compte)
    avant = _lignes(campagne)
    # Un compteur faussé : une ligne en trop sur une heure.
    Compteur.objects.filter(campagne_id=campagne.id).filter(emis__gt=0).update(emis=F("emis") + 1)

    ecarts = controle.controler_terminees()
    assert [e.campagne_id for e in ecarts] == [campagne.id]
    assert set(ecarts[0].colonnes) == {"emis"} and not ecarts[0].corrige
    assert _lignes(campagne) != avant  # signalé, pas corrigé
    with pytest.raises(CommandError, match="1 campagne"):
        call_command("controler_compteurs")

    ecart = controle.controler(campagne, corriger=True)
    assert ecart.corrige
    # Les mêmes lignes qu'au fil de l'eau, heure par heure, et le recalcul
    # rejoué ne change rien.
    assert _lignes(campagne) == avant
    recompter(compte.id, campagne.id, campagne.confirmee_le)
    assert _lignes(campagne) == avant
    assert controle.controler(campagne) is None
    call_command("controler_compteurs", "--campagne", str(campagne.id))


def test_les_tableaux_de_bord_ne_lisent_pas_le_journal_des_messages(compte):
    campagne = _terminee(compte)
    with (
        CaptureQueriesContext(connections["default"]) as defaut,
        CaptureQueriesContext(connections["worker"]) as worker,
    ):
        suivi = selectors.suivi(campagne)
        volume = journal_lectures.volume_mensuel(compte.id)
    assert suivi.partis == volume == 10
    requetes = [q["sql"] for q in defaut.captured_queries + worker.captured_queries]
    assert requetes and not any("journal_message" in sql for sql in requetes)


def test_un_accuse_tardif_pendant_le_controle_ne_fait_pas_d_ecart(compte):
    campagne, (tardif,) = _terminee(compte, garder=1)
    lire_journal = journal_lectures.compte_journal

    def puis_un_accuse(*args):
        lu = lire_journal(*args)

        # Validé par un autre processus entre la lecture du journal et celle
        # des compteurs.
        def appliquer():
            try:
                applique.append(appliquer_accuse(tardif))
            finally:
                connections.close_all()

        fil = threading.Thread(target=appliquer)
        fil.start()
        fil.join()
        return lu

    applique = []

    with mock.patch.object(journal_lectures, "compte_journal", side_effect=puis_un_accuse):
        assert controle.controler(campagne) is None
    assert applique == [True]
    reel = journal_lectures.compte_journal(
        compte.id, campagne.id, campagne.confirmee_le, campagne.terminee_le
    )
    assert reel["livres"] + reel["echecs"] == 10
    assert controle.controler(campagne) is None
