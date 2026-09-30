"""Listes et appartenance, T-49."""

import io
import random
import threading

import pytest
from django.db import connection, transaction

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts import imports, listes
from apps.contacts.models import Contact, Liste, ListeContact
from apps.contacts.services import creer_contact
from apps.core.cloisonnement import poser_client

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def client(en_tant_que_worker):
    return creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)


def _contacts(client, nombre, debut=0):
    return [
        creer_contact(client=client, numero=f"+2267{debut + n:07d}", origine="SAISIE").id
        for n in range(nombre)
    ]


def test_le_compteur_suit_les_ajouts_et_retraits(client):
    liste = listes.creer(client=client, nom="Clients fidèles")
    ids = _contacts(client, 5)
    assert listes.ajouter(liste, ids) == 5
    assert listes.ajouter(liste, ids[:2]) == 0  # déjà membres
    assert listes.retirer(liste, ids[:3]) == 3
    liste.refresh_from_db()
    assert liste.nombre_contacts == 2 == listes.recompter(liste)


def test_le_compteur_reste_juste_sous_des_ajouts_et_retraits_concurrents(client):
    liste = listes.creer(client=client, nom="Concurrence")
    ids = _contacts(client, 60)
    depart = threading.Barrier(6)

    def travailler(graine):
        hasard = random.Random(graine)
        try:
            depart.wait(10)
            for _ in range(15):
                lot = hasard.sample(ids, 8)
                if hasard.random() < 0.6:
                    listes.ajouter(liste, lot)
                else:
                    listes.retirer(liste, lot)
        finally:
            connection.close()

    fils = [threading.Thread(target=travailler, args=(g,)) for g in range(6)]
    for fil in fils:
        fil.start()
    for fil in fils:
        fil.join(60)
    liste.refresh_from_db()
    assert liste.nombre_contacts == listes.recompter(liste)


def test_un_contact_retire_d_une_liste_reste_dans_le_repertoire(client):
    liste = listes.creer(client=client, nom="Temporaire")
    ids = _contacts(client, 1)
    listes.ajouter(liste, ids)
    listes.retirer(liste, ids)
    assert Contact.objects.filter(id=ids[0]).exists()


def test_supprimer_une_liste_ne_supprime_pas_ses_contacts(client):
    liste = listes.creer(client=client, nom="À supprimer")
    ids = _contacts(client, 3)
    listes.ajouter(liste, ids)
    assert listes.supprimer(liste) == 3
    assert not Liste.objects.filter(id=liste.id).exists()
    assert not ListeContact.objects.filter(contact_id__in=ids).exists()
    assert Contact.objects.filter(id__in=ids).count() == 3


def test_on_n_ajoute_pas_le_contact_d_un_autre_compte(client):
    liste = listes.creer(client=client, nom="Mienne")
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    etranger = _contacts(autre, 1, debut=500)
    assert listes.ajouter(liste, etranger) == 0


def test_un_import_ajoute_en_masse_ses_contacts_a_une_liste(client):
    deja = _contacts(client, 1, debut=100)  # connu avant l'import, présent dans le fichier
    liste = listes.creer(client=client, nom="Salon 2026")
    texte = "numero\n+22670000100\n+22670000101\n+22670000102\n123\n"
    import_ = imports.deposer(client=client, flux=io.BytesIO(texte.encode()), nom_origine="s.csv")
    mappage = imports.proposer_mappage(imports.apercu(import_)) | {"liste_id": str(liste.id)}
    imports.lancer(import_, mappage)
    imports.traiter(import_.id)
    liste.refresh_from_db()
    assert liste.nombre_contacts == 3 == listes.recompter(liste)
    assert ListeContact.objects.filter(liste=liste, contact_id=deja[0]).exists()


def test_une_liste_d_un_autre_compte_est_refusee_a_l_import(client):
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    liste_etrangere = listes.creer(client=autre, nom="Étrangère")
    import_ = imports.deposer(
        client=client, flux=io.BytesIO(b"numero\n70123456\n"), nom_origine="s.csv"
    )
    mappage = imports.proposer_mappage(imports.apercu(import_)) | {
        "liste_id": str(liste_etrangere.id)
    }
    with pytest.raises(imports.MappageInvalide, match="Liste introuvable"):
        imports.lancer(import_, mappage)


def test_les_listes_sont_cloisonnees():
    """Sous le rôle applicatif : un client posé ne voit pas la liste d'un autre."""
    premier = creer_client(raison_sociale="Premier SA", forme=Client.Forme.SOCIETE)
    second = creer_client(raison_sociale="Second SA", forme=Client.Forme.SOCIETE)
    with transaction.atomic():
        poser_client(premier.id)
        listes.creer(client=premier, nom="Privée")
    with transaction.atomic():
        poser_client(second.id)
        assert not Liste.objects.exists()
    with transaction.atomic():
        poser_client(premier.id)
        assert Liste.objects.count() == 1
