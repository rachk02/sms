"""Point de contrôle du lot 4 (T-54).

Un import réel de deux cent mille lignes, par le vrai stockage objet : trois
opérateurs, des doublons, des numéros invalides et deux champs personnalisés.
Puis une segmentation et un export sur ce jeu. Les préfixes sont fictifs : la
table réelle se charge par `manage.py charger_prefixes`, jamais dans le code.
"""

import csv
import tempfile
import time
import tracemalloc
from collections import Counter

import pytest
from django.db import connections

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts import champs, imports, recherche, segments
from apps.contacts.models import Contact, Export, ImportRejet
from apps.contacts.recherche import Filtres
from apps.core.models import EvenementSortant
from apps.telecom.models import Prefixe

pytestmark = pytest.mark.django_db(
    transaction=True, databases=["default", "worker", "proprietaire"]
)

LIGNES = 200_000
PREFIXES = {"70": "ORANGE", "60": "MOOV", "58": "TELECEL"}
SECTEURS = ("riz", "coton", "karite", "sesame")


@pytest.fixture
def client(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        Prefixe(pays="BF", prefixe=prefixe, operateur=operateur)
        for prefixe, operateur in PREFIXES.items()
    )
    yield creer_client(raison_sociale="Coopérative SA", forme=Client.Forme.SOCIETE)
    Prefixe.objects.filter(pays="BF").delete()


def _fichier(chemin) -> dict:
    """Écrit le fichier et rend ce que l'import doit en tirer."""
    attendu = {"valides": set(), "doublons": 0, "rejets": 0, "operateurs": Counter()}
    attendu["riz_orange"] = 0
    prefixes = list(PREFIXES)
    with open(chemin, "w", encoding="utf-8", newline="") as sortie:
        ecrivain = csv.writer(sortie, delimiter=";")
        ecrivain.writerow(["Téléphone", "Nom", "Ville", "Code client", "Secteur", "Remarque"])
        for n in range(LIGNES):
            if n % 97 == 13:
                numero = f"{n % 10000}"  # trop court
            elif n % 50 == 49:
                numero = (
                    f"+226 {prefixes[(n - 1) % 3]} {(n - 1):06d}"  # doublon de la ligne d'avant
                )
            else:
                numero = f"{prefixes[n % 3]} {n:06d}"
            secteur = SECTEURS[n % 4]
            ecrivain.writerow([numero, f"Nom {n}", "Bobo", f"C{n}", secteur, "non importée"])
            chiffres = numero.replace(" ", "").removeprefix("+226")
            if len(chiffres) != 8:
                attendu["rejets"] += 1
            elif chiffres in attendu["valides"]:
                attendu["doublons"] += 1
            else:
                attendu["valides"].add(chiffres)
                operateur = PREFIXES[chiffres[:2]]
                attendu["operateurs"][operateur] += 1
                attendu["riz_orange"] += operateur == "ORANGE" and secteur == "riz"
    return attendu


def test_import_segmentation_et_export_sur_deux_cent_mille_lignes(client):
    champs.declarer(client=client, libelle="Code client", valeur_repli="sans code")
    champs.declarer(client=client, libelle="Secteur", valeur_repli="votre secteur")
    with tempfile.TemporaryDirectory() as dossier:
        chemin = f"{dossier}/cooperative.csv"
        attendu = _fichier(chemin)
        with open(chemin, "rb") as flux:
            import_ = imports.deposer(client=client, flux=flux, nom_origine="cooperative.csv")

    # Le mappage proposé reconnaît les champs déclarés, et eux seuls.
    mappage = imports.proposer_mappage(imports.apercu(import_), champs=champs.noms_declares(client))
    assert mappage["colonnes"] == {"numero": 0, "nom": 1, "ville": 2}
    assert mappage["personnalises"] == {"code_client": 3, "secteur": 4}
    imports.lancer(import_, mappage)

    tracemalloc.start()
    debut = time.perf_counter()
    fini = imports.traiter(import_.id)
    duree = time.perf_counter() - debut
    _, pic = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    print(f"import : {duree:.1f} s, pic mémoire {pic / 1024 / 1024:.1f} Mio")
    assert pic < 64 * 1024 * 1024

    # Le compte tient, ligne pour ligne.
    assert (fini.lignes_total, fini.lignes_acceptees, fini.doublons, fini.lignes_rejetees) == (
        LIGNES,
        len(attendu["valides"]),
        attendu["doublons"],
        attendu["rejets"],
    )
    # La répartition par opérateur suit la table des préfixes.
    repartition = Counter(
        Contact.objects.filter(client=client).values_list("operateur_presume", flat=True)
    )
    assert repartition == attendu["operateurs"]
    # Le rapport de rejets est exploitable : une ligne par rejet, avec son motif.
    rapport = "".join(imports.rapport_csv(fini)).splitlines()
    assert len(rapport) == attendu["rejets"] + 1
    assert all(";numéro trop court;" in ligne for ligne in rapport[1:])
    assert ImportRejet.objects.filter(import_lie=fini).count() == attendu["rejets"]

    with connections["proprietaire"].cursor() as curseur:
        curseur.execute("ANALYZE contacts_contact")
    criteres = [
        {"champ": "operateur", "operation": "egal", "valeur": "ORANGE"},
        {"champ": "secteur", "operation": "egal", "valeur": "riz"},
    ]
    debut = time.perf_counter()
    _, nombre = segments.creer(client=client, nom="Riziers Orange", criteres=criteres)
    duree = time.perf_counter() - debut
    print(f"segment : {nombre} contacts en {duree:.2f} s")
    assert nombre == attendu["riz_orange"]
    assert duree < 2

    utilisateur = creer_utilisateur(
        courriel="u@cooperative.bf", mot_de_passe="x", nom="U", client=client
    )
    export = recherche.demander_export(
        client=client, demandeur=utilisateur, filtres=Filtres(operateur="MOOV")
    )
    assert EvenementSortant.objects.filter(cle_idempotence=f"export:{export.id}").exists()
    fini = recherche.produire(export.id)
    assert (fini.etat, fini.lignes) == (Export.Etat.TERMINE, attendu["operateurs"]["MOOV"])
