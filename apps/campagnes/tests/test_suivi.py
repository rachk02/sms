"""Compteurs du suivi en direct (T-67).

Écrits dans la transaction de chaque changement d'état d'un message : ils
rendent les mêmes chiffres que le journal, rejeux compris, sans jamais le lire.
"""

import uuid

import pytest
from django.db.models import Count
from django.test import override_settings
from django.utils import timezone

from apps.campagnes import diffusion, selectors, services
from apps.campagnes.tests.test_pause import _en_file  # mêmes messages de file
from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts import liste_noire, listes
from apps.contacts.services import creer_contact
from apps.core.etats import Etat
from apps.credits import services as credits
from apps.journal.models import Compteur, Message
from apps.journal.services import appliquer_accuse
from apps.telecom.models import Connecteur, Prefixe
from workers.envoi import WorkerEnvoi

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("reglages"),
]

PRIX = 20


@pytest.fixture
def reglages():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=PRIX, SMS_NUMERO_COURT="36000"):
        yield


@pytest.fixture
def client(en_tant_que_worker):
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
    yield client
    Prefixe.objects.filter(pays="BF").delete()


def _confirmee(client, numeros: list[str]):
    liste = listes.creer(client=client, nom="Clients")
    listes.ajouter(
        liste, [creer_contact(client=client, numero=n, origine="SAISIE").id for n in numeros]
    )
    campagne = services.creer(
        client=client, auteur=None, nom="Promo", texte="Promotion", listes=[liste.id]
    )
    vu = services.apercu(campagne)
    return services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )


def _journal(campagne) -> dict:
    return dict(
        Message.objects.filter(campagne_id=campagne.id)
        .values_list("etat")
        .annotate(n=Count("id"))
        .values_list("etat", "n")
    )


def test_les_compteurs_rendent_les_chiffres_du_journal_rejeux_compris(client):
    numeros = [f"70{n:06d}" for n in range(8)] + [f"60{n:06d}" for n in range(4)]
    campagne = _confirmee(client, numeros)
    diffusion.publier()
    file = _en_file(campagne)
    # Deux numéros passent sur liste noire après la confirmation : refusés à la soumission.
    for numero in ("70000000", "60000000"):
        liste_noire.bloquer(client=client, numero=numero, origine="SAISIE")
    worker = WorkerEnvoi(files=[])
    for donnees in file[:9]:
        worker._soumettre(donnees)
    for donnees in file[:9]:  # la file relivre : aucun compte en double
        worker._soumettre(donnees)
    services.annuler(campagne, auteur=None)
    diffusion.publier()  # annule les trois restants et clôt
    # Les accusés, puis leur rejeu.
    accuses = [
        accuse
        for passerelle in worker.passerelles.values()
        for accuse in passerelle.accuses_dus(timezone.now() + timezone.timedelta(hours=1))
    ]
    for accuse in accuses + accuses:
        appliquer_accuse(accuse)
    campagne.refresh_from_db()
    journal = _journal(campagne)
    suivi = selectors.suivi(campagne)
    assert suivi.partis == journal.get(Etat.LIVRE, 0) + journal.get(Etat.NON_LIVRE, 0) == 7
    assert (suivi.livres, suivi.echecs) == (journal.get(Etat.LIVRE, 0), journal[Etat.NON_LIVRE])
    assert suivi.refuses == journal[Etat.REJETE] == 2
    assert suivi.annules == journal[Etat.ANNULE] == 3
    assert suivi.non_soumis == 0
    assert suivi.cout_consomme == campagne.cout_reel == 7 * PRIX
    reseaux = {(op, exp): n for op, exp, n in suivi.par_expediteur}
    attendus = {
        (operateur, "36000"): journal_par_reseau(campagne, operateur)
        for operateur in ("ORANGE", "MOOV")
    }
    assert reseaux == {cle: n for cle, n in attendus.items() if n}


def journal_par_reseau(campagne, operateur) -> int:
    return Message.objects.filter(
        campagne_id=campagne.id, operateur=operateur, date_soumission__isnull=False
    ).count()


def test_le_suivi_ne_lit_que_les_compteurs_selon_le_plan_d_execution(client):
    campagne = _confirmee(client, ["70000001", "70000002"])
    requete = (
        Compteur.objects.filter(client_id=campagne.client_id, campagne_id=campagne.id)
        .values("operateur", "expediteur_libelle")
        .annotate(emis=Count("emis"))
    )
    plan = requete.explain()
    assert "journal_compteur" in plan
    assert "journal_message" not in plan and "journal_destinataire" not in plan
