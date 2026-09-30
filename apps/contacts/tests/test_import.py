"""Import de contacts : mappage (T-46), traitement (T-47), rejets (T-48).

Contre le vrai MinIO du docker-compose, sous le rôle du worker d'import.
"""

import io
from unittest import mock

import pytest
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts import champs, imports
from apps.contacts.models import Contact, Import, ImportRejet, ImportTransit
from apps.contacts.services import creer_contact
from apps.telecom.models import Prefixe

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def client(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        [
            Prefixe(pays="BF", prefixe="70", operateur="ORANGE"),
            Prefixe(pays="BF", prefixe="71", operateur="MOOV"),
        ]
    )
    yield creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    Prefixe.objects.filter(pays="BF").delete()


def _deposer(client, texte, nom="contacts.csv"):
    return imports.deposer(client=client, flux=io.BytesIO(texte.encode()), nom_origine=nom)


FICHIER = (
    "Téléphone;Nom;Prénom;Ville;Entreprise\n"
    "70 12 34 56;Ouédraogo;Awa;Ouagadougou;Faso Riz\n"
    "+226 71.00.00.01;Sawadogo;Moussa;Bobo;\n"
    "7012345;Kaboré;Ali;Koudougou;\n"
    ";Traoré;Fati;Banfora;\n"
    "70123456;Doublon;Du fichier;;\n"
    "0022670999999;Zongo;Issa;Ouahigouya;Coop\n"
)


def test_l_apercu_montre_les_vingt_premieres_lignes_et_propose_un_mappage(client):
    import_ = _deposer(client, "".join(f"70{n:06d};Nom {n}\n" for n in range(30)))
    lignes = imports.apercu(import_)
    assert len(lignes) == 20
    mappage = imports.proposer_mappage(lignes)
    assert mappage["entetes"] is False and mappage["colonnes"] == {"numero": 0}


def test_les_entetes_sont_reconnues_et_seuls_les_champs_declares_sont_proposes(client):
    import_ = _deposer(client, FICHIER)
    mappage = imports.proposer_mappage(imports.apercu(import_))
    assert mappage["entetes"] is True
    assert mappage["colonnes"] == {"numero": 0, "nom": 1, "prenom": 2, "ville": 3}
    assert mappage["personnalises"] == {}  # « Entreprise » n'est pas déclaré
    mappage = imports.proposer_mappage(imports.apercu(import_), champs={"entreprise"})
    assert mappage["personnalises"] == {"entreprise": 4}


def test_une_colonne_ne_nourrit_pas_un_champ_non_declare(client):
    import_ = _deposer(client, FICHIER)
    mappage = {"entetes": True, "colonnes": {"numero": 0}, "personnalises": {"entreprise": 4}}
    with pytest.raises(imports.MappageInvalide, match="non déclarés : entreprise"):
        imports.lancer(import_, mappage)


def test_un_fichier_sans_entetes_se_mappe_par_position(client):
    import_ = _deposer(client, "Awa;70123456;Ouaga\nMoussa;71000001;Bobo\n")
    mappage = imports.proposer_mappage(imports.apercu(import_))
    assert mappage["colonnes"]["numero"] == 1


def test_aucun_import_ne_demarre_sans_mappage_confirme(client):
    import_ = _deposer(client, FICHIER)
    with pytest.raises(imports.MappageInvalide, match="mappage confirmé"):
        imports.traiter(import_.id)
    with pytest.raises(imports.MappageInvalide, match="colonne qui porte le numéro"):
        imports.lancer(import_, {"entetes": True, "colonnes": {"nom": 1}})


def _importer(client, texte, **kw):
    import_ = _deposer(client, texte, **kw)
    mappage = imports.proposer_mappage(imports.apercu(import_), champs=champs.noms_declares(client))
    imports.lancer(import_, mappage)
    return imports.traiter(import_.id)


def test_le_compte_final_egale_le_nombre_de_lignes_du_fichier(client):
    creer_contact(client=client, numero="+22670999999", origine="SAISIE")  # déjà connu
    champs.declarer(client=client, libelle="Entreprise", valeur_repli="votre entreprise")
    fini = _importer(client, FICHIER)
    assert fini.etat == Import.Etat.TERMINE
    assert (fini.lignes_total, fini.lignes_acceptees, fini.lignes_rejetees, fini.doublons) == (
        6,
        2,
        2,
        2,
    )
    assert fini.lignes_acceptees + fini.lignes_rejetees + fini.doublons == fini.lignes_total
    awa = Contact.objects.get(client=client, numero_normalise="+22670123456")
    assert (awa.nom, awa.prenom, awa.operateur_presume, awa.origine) == (
        "Ouédraogo",
        "Awa",
        "ORANGE",
        "IMPORT",
    )
    assert awa.champs == {"entreprise": "Faso Riz"}
    assert not ImportTransit.objects.exists()


def test_chaque_rejet_porte_un_motif_explicite(client):
    fini = _importer(client, FICHIER)
    motifs = dict(ImportRejet.objects.filter(import_lie=fini).values_list("numero_ligne", "motif"))
    assert motifs == {4: "numéro trop court", 5: "colonne du numéro vide"}


def test_le_rapport_des_rejets_se_telecharge_et_se_reimporte_apres_correction(client):
    fini = _importer(client, FICHIER)
    rapport = "".join(imports.rapport_csv(fini))
    assert rapport.startswith("numero_ligne;motif;ligne_d_origine\n")
    assert "numéro trop court" in rapport and "Kaboré" in rapport
    corrige = "Téléphone;Nom\n70123499;Kaboré\n71555555;Traoré\n"
    refait = _importer(client, corrige, nom="corrige.csv")
    assert (refait.lignes_acceptees, refait.lignes_rejetees) == (2, 0)


def test_un_import_entierement_rejete_explique_la_cause_dominante(client):
    fini = _importer(client, "numero\n123\n456\n\n789\nabc\n")
    assert fini.lignes_acceptees == 0 and fini.lignes_rejetees == 4
    assert imports.cause_dominante(fini) == "numéro trop court"


def test_un_import_interrompu_n_integre_rien_puis_reprend_proprement(client):
    texte = "numero\n" + "".join(f"70{n:06d}\n" for n in range(2500))
    import_ = _deposer(client, texte)
    imports.lancer(import_, imports.proposer_mappage(imports.apercu(import_)))

    reel = imports._ecrire_paquet
    appels = []

    def mourir_au_deuxieme_paquet(*args):
        appels.append(1)
        if len(appels) == 2:
            raise RuntimeError("worker tué")
        reel(*args)

    with mock.patch.object(imports, "_ecrire_paquet", mourir_au_deuxieme_paquet):
        with pytest.raises(RuntimeError):
            imports.traiter(import_.id)
    assert not Contact.objects.filter(client=client).exists(), "lignes à moitié intégrées"
    assert Import.objects.get(id=import_.id).lignes_total == 1000  # progression réelle

    fini = imports.traiter(import_.id)  # relivré par le courtier
    assert (fini.lignes_total, fini.lignes_acceptees) == (2500, 2500)
    assert Contact.objects.filter(client=client).count() == 2500


def test_un_fichier_abime_fait_echouer_l_import_sans_rien_integrer(client):
    # Des octets nuls en plein fichier : ce n'est plus un texte.
    abime = ("numero\n" + "".join(f"70{n:06d}\n" for n in range(1500))).encode() + b"70\x00\x00\n"
    import_ = imports.deposer(client=client, flux=io.BytesIO(abime), nom_origine="abime.csv")
    imports.lancer(import_, imports.proposer_mappage(imports.apercu(import_)))
    fini = imports.traiter(import_.id)
    assert fini.etat == Import.Etat.ECHOUE and "ligne 1502" in fini.erreur
    assert not Contact.objects.filter(client=client).exists()


def test_le_mappage_d_un_import_de_meme_forme_est_repris(client):
    code = champs.declarer(client=client, libelle="Code client", valeur_repli="inconnu")
    premier = _importer(client, "Tel;Nom;Code client\n70111111;Awa;C1\n")
    retouche = dict(premier.mappage, personnalises={"code_client": 2}, colonnes={"numero": 0})
    Import.objects.filter(id=premier.id).update(mappage=retouche, termine_le=timezone.now())
    second = _deposer(client, "Tel;Nom;Code client\n71222222;Moussa;C2\n")
    assert imports.mappage_precedent(client, imports.apercu(second)) == retouche
    # Le champ supprimé depuis ne se propose plus.
    champs.supprimer(code)
    repris = imports.mappage_precedent(client, imports.apercu(second))
    assert repris["personnalises"] == {} and repris["colonnes"] == {"numero": 0}


def test_un_fichier_au_type_non_admis_est_refuse_a_son_contenu(client):
    from apps.fichiers.services import FichierRefuse

    with pytest.raises(FichierRefuse, match="reconnu au contenu"):
        imports.deposer(
            client=client, flux=io.BytesIO(b"%PDF-1.7 pas un import"), nom_origine="liste.csv"
        )
