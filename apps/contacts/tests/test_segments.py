"""Champs personnalisés (T-52) et segments par critères (T-50).

Sous le rôle du worker, qui ignore le cloisonnement : le filtre par client des
segments doit tenir de lui-même, comme à la confirmation d'une campagne.
"""

import time
from datetime import timedelta

import pytest
from django.db import connection, connections
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts import champs, liste_noire, listes, segments
from apps.contacts.models import ChampPersonnalise, Contact, Segment
from apps.contacts.services import creer_contact

pytestmark = pytest.mark.django_db(
    transaction=True, databases=["default", "worker", "proprietaire"]
)


@pytest.fixture
def client(en_tant_que_worker):
    return creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)


def _contact(client, numero, **kw):
    contact = creer_contact(client=client, numero=numero, origine="SAISIE")
    if kw:
        Contact.objects.filter(id=contact.id).update(**kw)
    return contact


# Champs personnalisés


def test_une_valeur_de_repli_vide_est_refusee(client):
    with pytest.raises(champs.ChampRefuse, match="valeur de repli est obligatoire"):
        champs.declarer(client=client, libelle="Entreprise", valeur_repli="  ")


def test_dix_champs_au_plus_par_client(client):
    for n in range(ChampPersonnalise.MAXIMUM_PAR_CLIENT):
        champs.declarer(client=client, libelle=f"Champ {n}", valeur_repli="x")
    with pytest.raises(champs.ChampRefuse, match="10 champs personnalisés au plus"):
        champs.declarer(client=client, libelle="Onzième", valeur_repli="x")


def test_un_champ_ne_prend_pas_le_nom_d_une_donnee_du_contact(client):
    with pytest.raises(champs.ChampRefuse, match="désigne déjà une donnée du contact"):
        champs.declarer(client=client, libelle="Ville", valeur_repli="x")
    with pytest.raises(champs.ChampRefuse, match="au moins une lettre"):
        champs.declarer(client=client, libelle="???", valeur_repli="x")


def test_le_libelle_et_l_entete_donnent_le_meme_nom(client):
    champ = champs.declarer(client=client, libelle="Référence client", valeur_repli="x")
    assert champ.nom == "reference_client" == champs.nom_de("reference-client")


def test_un_champ_supprime_desactive_les_segments_qui_s_en_servaient(client):
    _contact(client, "70000001", champs={"secteur": "riz"}, ville="Bobo")
    secteur = champs.declarer(client=client, libelle="Secteur", valeur_repli="votre secteur")
    riz, _ = segments.creer(
        client=client,
        nom="Riziers",
        criteres=[{"champ": "secteur", "operation": "egal", "valeur": "riz"}],
    )
    bobo, _ = segments.creer(
        client=client,
        nom="Bobo",
        criteres=[{"champ": "ville", "operation": "egal", "valeur": "Bobo"}],
    )
    assert champs.supprimer(secteur) == [riz]
    riz.refresh_from_db()
    assert not riz.actif and "« Secteur » a été supprimé" in riz.motif_desactivation
    with pytest.raises(segments.SegmentInvalide, match="a été supprimé"):
        segments.recalculer(riz)
    assert Segment.objects.get(id=bobo.id).actif
    # Les valeurs saisies restent sur le contact.
    assert Contact.objects.get(client=client).champs == {"secteur": "riz"}


# Segments


def test_l_estimation_est_ce_que_la_diffusion_enverra(client):
    _contact(client, "70000001", ville="Bobo")
    _contact(client, "70000002", ville="Bobo")
    _contact(client, "70000003", ville="Bobo", actif=False)
    _contact(client, "71000004", ville="Ouaga")
    liste_noire.bloquer(client=client, numero="70000002", origine="MANUEL")
    criteres = [{"champ": "ville", "operation": "parmi", "valeur": ["Bobo", "Banfora"]}]
    segment, estime = segments.creer(client=client, nom="Ouest", criteres=criteres)
    assert estime == segment.nombre_estime == 1
    assert len(segments.figer(segment)) == estime


def test_les_criteres_se_combinent(client):
    ancien = timezone.now() - timedelta(days=40)
    _contact(client, "70000001", operateur_presume="ORANGE", cree_le=ancien)
    _contact(client, "70000002", operateur_presume="ORANGE")
    _contact(client, "71000003", operateur_presume="MOOV", operateur_confirme="ORANGE")
    _contact(client, "70000004", operateur_presume="ORANGE", operateur_confirme="MOOV")
    fideles = listes.creer(client=client, nom="Fidèles")
    listes.ajouter(fideles, Contact.objects.filter(client=client).values_list("id", flat=True))
    depuis = (timezone.localdate() - timedelta(days=7)).isoformat()
    criteres = [
        {"champ": "operateur", "operation": "egal", "valeur": "ORANGE"},
        {"champ": "date_entree", "operation": "apres", "valeur": depuis},
        {"champ": "liste", "operation": "egal", "valeur": str(fideles.id)},
    ]
    numeros = set(segments.contacts(client, criteres).values_list("numero_normalise", flat=True))
    # L'opérateur confirmé l'emporte sur le présumé ; le contact ancien est écarté.
    assert numeros == {"+22670000002", "+22671000003"}


def test_un_segment_ne_deborde_jamais_sur_un_autre_client(client):
    autre = creer_client(raison_sociale="Autre SA", forme=Client.Forme.SOCIETE)
    _contact(client, "70000001", ville="Bobo")
    _contact(autre, "70000002", ville="Bobo")
    sa_liste = listes.creer(client=autre, nom="Autre")
    listes.ajouter(sa_liste, Contact.objects.filter(client=autre).values_list("id", flat=True))
    par_ville = [{"champ": "ville", "operation": "egal", "valeur": "Bobo"}]
    assert segments.contacts(client, par_ville).count() == 1
    par_liste = [{"champ": "liste", "operation": "egal", "valeur": str(sa_liste.id)}]
    assert not segments.contacts(client, par_liste).exists()


def test_un_segment_vide_est_signale_des_sa_creation(client):
    _contact(client, "70000001", ville="Bobo")
    with pytest.raises(segments.SegmentInvalide, match="le segment serait vide"):
        segments.creer(
            client=client,
            nom="Personne",
            criteres=[{"champ": "ville", "operation": "egal", "valeur": "Dori"}],
        )
    assert not Segment.objects.exists()


@pytest.mark.parametrize(
    "critere, message",
    [
        ({"champ": "ville", "operation": "parmi", "valeur": "Bobo"}, "attend une liste"),
        ({"champ": "date_entree", "operation": "avant", "valeur": "hier"}, "Date invalide"),
        ({"champ": "couleur", "operation": "egal", "valeur": "bleu"}, "Critère inconnu"),
    ],
)
def test_un_critere_mal_forme_est_explique(client, critere, message):
    with pytest.raises(segments.SegmentInvalide, match=message):
        segments.contacts(client, [critere])


def test_un_champ_personnalise_se_compare_en_texte(client):
    _contact(client, "70000001", champs={"age": "30"})
    champs.declarer(client=client, libelle="Age", valeur_repli="x", type_="NOMBRE")
    criteres = [{"champ": "age", "operation": "egal", "valeur": 30}]
    assert segments.contacts(client, criteres).count() == 1


def test_un_segment_sur_cent_mille_contacts_revient_en_moins_de_deux_secondes(client):
    with connection.cursor() as curseur:
        curseur.execute(
            """
            INSERT INTO contacts_contact (
                id, client_id, numero_normalise, numero_origine, nom, prenom, ville,
                operateur_presume, operateur_confirme, champs, origine, consentement_source,
                actif, cree_le, modifie_le
            )
            SELECT gen_random_uuid(), %s, '+2267' || lpad(n::text, 7, '0'), n::text, '', '',
                   (ARRAY['Ouaga', 'Bobo', 'Koudougou'])[1 + n %% 3],
                   (ARRAY['ORANGE', 'MOOV', 'TELECEL'])[1 + n %% 3], '',
                   jsonb_build_object('secteur', (ARRAY['riz', 'coton'])[1 + n %% 2]),
                   'IMPORT', '', true, now() - (n %% 90) * interval '1 day', now()
            FROM generate_series(1, 100000) AS n
            """,
            [client.id],
        )
    # Le propriétaire seul peut analyser la table ; sous un autre rôle, ANALYZE
    # ne fait rien et le planificateur raisonne sans statistiques.
    with connections["proprietaire"].cursor() as curseur:
        curseur.execute("ANALYZE contacts_contact")
    champs.declarer(client=client, libelle="Secteur", valeur_repli="x")
    criteres = [
        {"champ": "operateur", "operation": "parmi", "valeur": ["ORANGE", "MOOV"]},
        {"champ": "secteur", "operation": "egal", "valeur": "riz"},
    ]
    debut = time.perf_counter()
    _, nombre = segments.creer(client=client, nom="Gros", criteres=criteres)
    duree = time.perf_counter() - debut
    assert nombre > 30000
    assert duree < 2, f"{duree:.2f} s"
