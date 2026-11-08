"""Recherche, pagination par curseur et export de contacts (T-53).

Sous le rôle du worker, contre le vrai MinIO du docker-compose.
"""

from datetime import timedelta

import pytest
from django.core import mail
from django.db import connection, connections, transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts import champs, listes, recherche
from apps.contacts.models import Contact, Export
from apps.contacts.recherche import Filtres
from apps.contacts.services import creer_contact
from apps.core.models import Audit, EvenementSortant
from apps.fichiers import services as fichiers
from apps.fichiers.models import Fichier

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


def _numeros(contacts):
    return {c.numero_normalise for c in contacts}


def _inserer(client, nombre):
    with connection.cursor() as curseur:
        curseur.execute(
            """
            INSERT INTO contacts_contact (
                id, client_id, numero_normalise, numero_origine, nom, prenom, ville,
                operateur_presume, operateur_confirme, champs, origine, consentement_source,
                actif, cree_le, modifie_le
            )
            SELECT gen_random_uuid(), %s, '+2267' || lpad(n::text, 7, '0'), n::text,
                   'Nom ' || n, '', 'Ouaga', 'ORANGE', '',
                   jsonb_build_object('code_client', 'C' || n), 'IMPORT', '', true,
                   now() - n * interval '1 second', now()
            FROM generate_series(1, %s) AS n
            """,
            [client.id, nombre],
        )
    # Le propriétaire seul peut analyser la table ; sous un autre rôle, ANALYZE
    # ne fait rien et le planificateur raisonne sans statistiques.
    with connections["proprietaire"].cursor() as curseur:
        curseur.execute("ANALYZE contacts_contact")


@pytest.mark.parametrize(
    "saisie, attendus",
    [
        ("70 12", ["+2267012"]),
        ("+226 70.12", ["+2267012"]),
        ("0022670", ["+22670"]),
        ("22670", ["+22670", "+22622670"]),
        ("abc", []),
    ],
)
def test_la_saisie_d_un_numero_se_ramene_au_format_international(saisie, attendus):
    assert recherche.prefixes_numero(saisie) == attendus


def test_la_recherche_par_numero_passe_par_l_index_de_la_colonne_normalisee(client):
    _inserer(client, 2000)
    requete = recherche.filtrer(client, Filtres(numero="70 00 01"))
    assert requete.count() == 100  # de +22670000100 à +22670000199
    for filtres, index in (
        (Filtres(numero="7000012"), "contact_numero_prefixe"),
        (Filtres(nom="nom 12"), "contact_nom_prefixe"),
    ):
        with transaction.atomic():
            # Une petite table se lit d'un bloc : on interdit la lecture
            # séquentielle pour voir le chemin que prendra la grande.
            connection.cursor().execute("SET LOCAL enable_seqscan = off")
            plan = recherche.filtrer(client, filtres).explain()
        assert index in plan and "Seq Scan" not in plan, plan


def test_les_filtres_se_combinent(client):
    ancien = timezone.now() - timedelta(days=30)
    awa = _contact(client, "70000001", nom="Ouédraogo", operateur_presume="ORANGE")
    _contact(client, "70000002", nom="Ouattara", operateur_presume="ORANGE", cree_le=ancien)
    _contact(client, "71000003", nom="Ouédraogo", operateur_presume="MOOV")
    clients_fideles = listes.creer(client=client, nom="Fidèles")
    listes.ajouter(clients_fideles, [awa.id])
    depuis = (timezone.localdate() - timedelta(days=7)).isoformat()
    assert _numeros(recherche.filtrer(client, Filtres(nom="ou", operateur="ORANGE"))) == {
        "+22670000001",
        "+22670000002",
    }
    assert _numeros(recherche.filtrer(client, Filtres(nom="OU", depuis=depuis))) == {
        "+22670000001",
        "+22671000003",
    }
    filtres = Filtres(liste_id=str(clients_fideles.id))
    assert _numeros(recherche.filtrer(client, filtres)) == {"+22670000001"}


def test_la_pagination_par_curseur_parcourt_tout_sans_doublon(client):
    _inserer(client, 120)
    vus, curseur, pages = [], "", 0
    while True:
        contacts, curseur = recherche.page(client, Filtres(), apres=curseur)
        vus += contacts
        pages += 1
        if not curseur:
            break
    assert pages == 3 and len(vus) == len({c.id for c in vus}) == 120
    assert vus == sorted(vus, key=lambda c: (c.cree_le, c.id), reverse=True)
    with pytest.raises(recherche.RechercheInvalide, match="Curseur"):
        recherche.page(client, Filtres(), apres="pas-un-curseur")


def test_au_dela_du_seuil_l_ecran_propose_l_export(client, monkeypatch):
    _inserer(client, 30)
    monkeypatch.setattr(recherche, "SEUIL_AFFICHAGE", 29)
    assert recherche.depasse_l_affichage(client, Filtres())
    assert not recherche.depasse_l_affichage(client, Filtres(numero="7000001"))


def test_un_export_de_cent_mille_contacts_part_en_file_et_revient_par_lien_signe(client):
    _inserer(client, 100_000)
    champs.declarer(client=client, libelle="Code client", valeur_repli="inconnu")
    demandeur = creer_utilisateur(courriel="u@exemple.bf", mot_de_passe="x", nom="U", client=client)
    export = recherche.demander_export(client=client, demandeur=demandeur, filtres=Filtres())
    assert export.etat == Export.Etat.EN_FILE
    evenement = EvenementSortant.objects.get(cle_idempotence=f"export:{export.id}")
    assert evenement.nom == recherche.FILE_EXPORT
    assert Audit.objects.filter(action="contacts.export.demande", auteur=demandeur).exists()

    fini = recherche.produire(export.id)
    assert (fini.etat, fini.lignes) == (Export.Etat.TERMINE, 100_000)
    fichier = Fichier.objects.get(id=fini.fichier_id)
    assert fichier.famille == Fichier.Famille.EXPORT
    with fichiers.ouvrir(fichier) as flux:
        entete = flux.readline().decode("utf-8-sig")
        premiere = flux.readline().decode()
    assert entete.strip() == "numero;nom;prenom;ville;operateur;entre_le;actif;Code client"
    assert premiere.startswith("+22670100000;Nom 100000;") and premiere.strip().endswith(";C100000")
    courriel = mail.outbox[-1]
    assert courriel.to == ["u@exemple.bf"] and "X-Amz-Signature" in courriel.body
    assert "100000 contacts" in courriel.body
    # Relivré par le courtier : rien ne se refait.
    envoyes = len(mail.outbox)
    assert recherche.produire(export.id).fichier_id == fini.fichier_id
    assert len(mail.outbox) == envoyes


def test_le_lien_d_export_ne_part_pas_vers_un_membre_retire(client):
    """Retiré entre sa demande et la production, le demandeur ne reçoit pas le
    lien vers le répertoire, et personne d'autre ne le reçoit à sa place."""
    from apps.comptes.models import Utilisateur
    from apps.comptes.services import retirer_membre

    _inserer(client, 3)
    administrateur = creer_utilisateur(
        courriel="chef@exemple.bf",
        mot_de_passe="x",
        nom="Chef",
        client=client,
        role=Utilisateur.Role.ADMIN,
    )
    demandeur = creer_utilisateur(courriel="u@exemple.bf", mot_de_passe="x", nom="U", client=client)
    export = recherche.demander_export(client=client, demandeur=demandeur, filtres=Filtres())
    retirer_membre(demandeur, auteur=administrateur)
    mail.outbox.clear()
    fini = recherche.produire(export.id)
    assert fini.etat == Export.Etat.TERMINE
    assert mail.outbox == []
