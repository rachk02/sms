"""Enregistrement d'un client par l'équipe, T-31 et T-37 (décision F-11 du 29 septembre 2026)."""

import pytest
from django.core import mail
from django.core.files.uploadedfile import SimpleUploadedFile

from apps.backoffice.services import EnregistrementRefuse, PieceDeposee, enregistrer_client
from apps.comptes.models import Client, Dossier, Piece, Utilisateur
from apps.comptes.services import creer_utilisateur
from apps.core.models import Audit
from apps.fichiers.models import Fichier

pytestmark = pytest.mark.django_db(transaction=True)

PDF = b"%PDF-1.7\n" + b"0" * 512


def _piece(nom="piece.pdf", contenu=PDF):
    return PieceDeposee(contenu, nom)


def _identite(**champs):
    return {
        "raison_sociale": "Exemple SA",
        "forme": Client.Forme.SOCIETE,
        "rccm": "BF-OUA-2026-B-0001",
        "ifu": "00012345A",
        "representant_nom": "Awa Ouédraogo",
        "representant_courriel": "awa@exemple.bf",
    } | champs


PIECES_SOCIETE = {
    "RCCM": _piece(),
    "IFU": _piece(),
    "PIECE_GERANT": _piece(),
    "DEMANDE_ECRITE": _piece(),
}


@pytest.fixture
def agent(en_tant_que_backoffice):
    return creer_utilisateur(
        courriel="agent@el-immeka.test",
        mot_de_passe="une-phrase-solide",
        nom="Agent",
        is_staff=True,
        is_superuser=True,
    )


def test_une_societe_sans_rccm_est_refusee_et_le_message_le_nomme(agent):
    with pytest.raises(EnregistrementRefuse) as refus:
        enregistrer_client(identite=_identite(rccm=""), pieces=PIECES_SOCIETE, agent=agent)
    assert "numéro RCCM est obligatoire" in str(refus.value)
    assert not Client.objects.exists()


def test_une_association_sans_recepisse_est_refusee_et_la_piece_est_nommee(agent):
    identite = _identite(forme=Client.Forme.ASSOCIATION, rccm="")
    pieces = {"IFU": _piece(), "PIECE_GERANT": _piece(), "DEMANDE_ECRITE": _piece()}
    with pytest.raises(EnregistrementRefuse) as refus:
        enregistrer_client(identite=identite, pieces=pieces, agent=agent)
    assert refus.value.erreurs == {
        "piece_RECEPISSE": ["Pièce manquante : Récépissé de déclaration."]
    }


def test_on_ne_reclame_pas_de_rccm_a_une_association(agent):
    identite = _identite(forme=Client.Forme.ASSOCIATION, rccm="", ifu="00099999Z")
    pieces = {
        "RECEPISSE": _piece(),
        "IFU": _piece(),
        "PIECE_GERANT": _piece(),
        "DEMANDE_ECRITE": _piece(),
    }
    dossier = enregistrer_client(identite=identite, pieces=pieces, agent=agent)
    assert dossier.client.rccm is None


def test_un_client_complet_est_enregistre_avec_son_dossier_ses_pieces_et_son_administrateur(agent):
    dossier = enregistrer_client(identite=_identite(), pieces=PIECES_SOCIETE, agent=agent)

    assert dossier.etat == Dossier.Etat.DEPOSE and dossier.depose_le
    assert set(Piece.objects.filter(dossier=dossier).values_list("type", flat=True)) == set(
        PIECES_SOCIETE
    )
    fichiers = Fichier.objects.filter(client=dossier.client)
    assert fichiers.count() == 4 and all(f.chiffre for f in fichiers)

    representant = Utilisateur.objects.get(courriel="awa@exemple.bf")
    assert representant.client_id == dossier.client_id
    assert representant.role == Utilisateur.Role.ADMIN
    assert not representant.has_usable_password()  # activé par son invitation (T-32)
    assert mail.outbox[-1].to == ["awa@exemple.bf"] and "/invitation/" in mail.outbox[-1].body

    audit = Audit.objects.get(action="comptes.client.enregistre")
    assert audit.auteur_id == agent.id and audit.client_id == dossier.client_id
    assert audit.valeur_apres["rccm"] == "BF-OUA-2026-B-0001"


def test_deux_enregistrements_du_meme_rccm_sont_refuses(agent):
    enregistrer_client(identite=_identite(), pieces=PIECES_SOCIETE, agent=agent)
    doublon = _identite(ifu="00077777B", representant_courriel="autre@exemple.bf")
    with pytest.raises(EnregistrementRefuse, match="déjà rattaché"):
        enregistrer_client(identite=doublon, pieces=PIECES_SOCIETE, agent=agent)
    assert Client.objects.count() == 1


def test_une_piece_au_contenu_non_admis_annule_tout_l_enregistrement(agent):
    pieces = PIECES_SOCIETE | {"IFU": _piece("ifu.pdf", b"MZ\x90\x00executable")}
    with pytest.raises(EnregistrementRefuse) as refus:
        enregistrer_client(identite=_identite(), pieces=pieces, agent=agent)
    assert "piece_IFU" in refus.value.erreurs
    assert (
        not Client.objects.exists()
        and not Utilisateur.objects.filter(client__isnull=False).exists()
    )


def _fichiers(**types):
    return {f"piece_{t}": SimpleUploadedFile(f"{t}.pdf", PDF, "application/pdf") for t in types}


def test_l_ecran_d_enregistrement_nomme_la_piece_manquante(client, agent):
    client.force_login(agent)
    assert client.get("/admin/comptes/client/add/").status_code == 200
    donnees = _identite(forme=Client.Forme.ASSOCIATION, rccm="") | _fichiers(
        IFU=1, PIECE_GERANT=1, DEMANDE_ECRITE=1
    )
    reponse = client.post("/admin/comptes/client/add/", donnees)
    assert reponse.status_code == 200
    assert "Pièce manquante : Récépissé de déclaration." in reponse.content.decode()


def test_l_ecran_d_enregistrement_mene_au_dossier_depose(client, agent):
    client.force_login(agent)
    donnees = _identite() | _fichiers(RCCM=1, IFU=1, PIECE_GERANT=1, DEMANDE_ECRITE=1)
    reponse = client.post("/admin/comptes/client/add/", donnees)
    dossier = Dossier.objects.get()
    assert reponse.status_code == 302 and str(dossier.id) in reponse["Location"]
