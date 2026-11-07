"""Recherche dans le journal des messages (T-93).

Toujours bornée sur les dates, paginée par curseur, et la recherche par numéro
passe par l'index de la colonne normalisée.
"""

import re
import uuid
from datetime import timedelta

import pytest
from django.db import connection, connections, transaction
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.core.etats import Etat
from apps.journal import selectors
from apps.journal.models import Message
from apps.journal.services import enregistrer_message

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

INSERER = """
INSERT INTO journal_message (
    id, client_id, numero_normalise, operateur, expediteur_libelle, texte, nature,
    encodage, segments, cout_unitaire, cout_total, etat, motif_echec, reference_smsc,
    cle_idempotence, date_reception, modifie_le
)
SELECT gen_random_uuid(), %s, '+22670' || lpad(n::text, 6, '0'), 'ORANGE', 'FASORIZ',
    'Promotion', 'CLIENT', 'GSM7', 1, 25, 25, %s, '', '', '',
    now() - make_interval(secs => n / 10.0), now()
FROM generate_series(1, %s) AS n
"""


def _inserer(client, nombre, etat=Etat.LIVRE):
    with connection.cursor() as curseur:
        curseur.execute(INSERER, [client.id, etat, nombre])


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


def _filtres(**params):
    aujourd_hui = timezone.localdate()
    return selectors.lire_filtres(
        {"debut": str(aujourd_hui - timedelta(days=1)), "fin": str(aujourd_hui), **params}
    )


@pytest.mark.parametrize(
    "params",
    [{"debut": "2026-11-01"}, {"fin": "2026-11-01"}, {"debut": "2026-11-05", "fin": "2026-11-01"}],
    ids=["sans fin", "sans debut", "a l envers"],
)
def test_aucune_recherche_sans_borne_de_dates(client, membre, params):
    with pytest.raises(selectors.RechercheRefusee):
        selectors.lire_filtres(params)
    client.force_login(membre)
    with CaptureQueriesContext(connections["default"]) as requetes:
        reponse = client.get("/journal", params)
    assert reponse.status_code == 200 and 'role="alert"' in reponse.content.decode()
    assert not any("journal_message" in q["sql"] for q in requetes.captured_queries)


def test_sans_aucune_date_la_semaine_en_cours_est_cherchee():
    filtres = selectors.lire_filtres({})
    assert filtres.jours == 7 and filtres.fin > timezone.now()


def test_au_dela_du_seuil_l_ecran_propose_l_export(client, membre):
    client.force_login(membre)
    params = {"debut": "2026-09-01", "fin": "2026-10-31", "etat": "LIVRE"}
    with CaptureQueriesContext(connections["default"]) as requetes:
        page = client.get("/journal", params).content.decode()
    assert "Exporter ces messages en CSV" in page and "etat=LIVRE" in page
    assert not any("journal_message" in q["sql"] for q in requetes.captured_queries)


def test_les_filtres_et_le_cloisonnement(compte):
    autre = creer_client(raison_sociale="Banque SA", forme=Client.Forme.SOCIETE)
    campagne = uuid.uuid7()
    commun = {
        "operateur": "ORANGE",
        "expediteur_libelle": "FASORIZ",
        "texte": "Promotion",
        "cout_unitaire": 25,
        "date_reception": timezone.now() - timedelta(minutes=30),
    }
    vise = enregistrer_message(client=compte, numero_normalise="+22670123456", **commun)
    enregistrer_message(client=compte, numero_normalise="+22670999999", **commun)
    enregistrer_message(client=autre, numero_normalise="+22670123456", **commun)
    Message.objects.filter(id=vise.id).update(campagne_id=campagne, etat=Etat.LIVRE)

    def ids(**params):
        page = selectors.rechercher(compte.id, _filtres(**params))
        return {m.id for m in page.messages}

    assert len(ids()) == 2
    assert ids(numero="70 12 34 56") == {vise.id}
    assert ids(etat="LIVRE") == {vise.id}
    assert ids(campagne=str(campagne)) == {vise.id}
    with pytest.raises(selectors.RechercheRefusee, match="Numéro invalide"):
        _filtres(numero="abc")


def test_la_pagination_par_curseur_parcourt_tout_sans_offset(compte):
    _inserer(compte, 120)
    filtres = _filtres()
    vus, apres, pages = [], None, 0
    with CaptureQueriesContext(connections["default"]) as requetes:
        while True:
            page = selectors.rechercher(compte.id, filtres, apres)
            vus += [(m.date_reception, m.id) for m in page.messages]
            pages += 1
            if not page.suivant:
                break
            apres = page.suivant
    assert pages == 3 and len(vus) == len(set(vus)) == 120
    assert vus == sorted(vus, reverse=True)
    assert not any("OFFSET" in q["sql"].upper() for q in requetes.captured_queries)


def _plan(requete) -> str:
    """Le plan exécuté, l'index préféré au balayage comme sur une table de
    production, où les messages se comptent en millions."""
    with transaction.atomic():
        with connection.cursor() as curseur:
            curseur.execute("SET LOCAL enable_seqscan = off")
            curseur.execute("SET LOCAL enable_bitmapscan = off")
        return requete.explain(analyze=True)


def test_la_page_cent_ne_lit_pas_plus_de_lignes_que_la_page_une(compte):
    taille = selectors.TAILLE_PAGE
    _inserer(compte, 100 * taille + 10)
    filtres = _filtres()
    # Le curseur de la page cent : le dernier message de la page quatre-vingt-dix-neuf.
    reperes = list(
        selectors.requete_de_recherche(compte.id, filtres).values_list("date_reception", "id")
    )
    moment, identifiant = reperes[99 * taille - 1]
    curseur = f"{moment.isoformat()}_{identifiant}"
    page_cent = selectors.rechercher(compte.id, filtres, curseur)
    assert len(page_cent.messages) == taille

    lus = []
    for apres in (None, curseur):
        plan = _plan(selectors.requete_de_recherche(compte.id, filtres, apres)[: taille + 1])
        assert "Limit" in plan
        rendues = re.findall(r"Index Scan[^\n]*actual time=\S+ rows=(\d+)", plan)
        ecartees = re.findall(r"Rows Removed by Filter: (\d+)", plan)
        assert rendues, plan
        lus.append(sum(int(n) for n in rendues + ecartees))
    # Une page lit sa taille, plus une, que ce soit la première ou la centième :
    # rien n'est relu puis écarté pour atteindre la page cent.
    assert lus[0] <= 2 * (taille + 1) and lus[1] <= 2 * (taille + 1), lus


def test_la_recherche_par_numero_passe_par_l_index_normalise(compte):
    _inserer(compte, 500)
    filtres = _filtres(numero="70000250")
    plan = _plan(selectors.requete_de_recherche(compte.id, filtres)[:51])
    conditions = [ligne for ligne in plan.splitlines() if "Index Cond" in ligne]
    assert any("numero_normalise" in ligne for ligne in conditions), plan
    assert "Seq Scan" not in plan
    assert [m.numero_normalise for m in selectors.rechercher(compte.id, filtres).messages] == [
        "+22670000250"
    ]


def test_l_ecran_affiche_une_page_et_le_lien_suivant(client, membre):
    _inserer(membre.client, 60, etat=Etat.NON_LIVRE)
    client.force_login(membre)
    page = client.get("/journal").content.decode()
    assert page.count("+22670") == 50
    assert "Page suivante" in page and "apres=" in page
    suivante = re.search(r'href="/journal\?([^"]+apres=[^"]+)"', page).group(1)
    page_deux = client.get("/journal?" + suivante.replace("&amp;", "&")).content.decode()
    assert page_deux.count("+22670") == 10 and "Page suivante" not in page_deux
    assert "Revenir au début" in page_deux
