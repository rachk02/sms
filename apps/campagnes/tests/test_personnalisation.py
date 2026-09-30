"""Modèles de message et balises (T-55), compteur au cas le plus long (T-56)."""

import random
import re

import pytest
from django.test import override_settings

from apps.campagnes import modeles
from apps.campagnes.estimation import estimer
from apps.campagnes.personnalisation import ModeleInvalide, compiler
from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.contacts import champs
from apps.contacts.models import Contact
from apps.core.encodage import Encodage, analyser

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


@pytest.fixture
def client(en_tant_que_worker):
    return creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)


def _contacts(client, lignes):
    Contact.objects.bulk_create(
        Contact(
            client=client,
            numero_normalise=f"+2267{n:07d}",
            numero_origine=str(n),
            origine="SAISIE",
            operateur_presume="ORANGE",
            **champs_,
        )
        for n, champs_ in enumerate(lignes)
    )
    return Contact.objects.filter(client=client)


# Balises


@pytest.mark.parametrize(
    "texte, message",
    [
        ("Bonjour {prenom}, merci.", "demande une valeur de repli"),
        ("Bonjour {prenom|}, merci.", "demande une valeur de repli"),
        ("Votre code {code_client|x}", "Balise inconnue : {code_client}"),
        ("Promo {prenom|ami} -20 % {", "Accolade mal fermée"),
        ("   ", "Le message est vide"),
    ],
)
def test_une_balise_sans_repli_inconnue_ou_mal_fermee_est_refusee(texte, message):
    with pytest.raises(ModeleInvalide, match=re.escape(message)):
        compiler(texte, {})


def test_un_champ_vide_donne_la_valeur_de_repli():
    gabarit = compiler("Bonjour {prenom|cher client}, votre code {code}.", {"code": "à venir"})
    vide = Contact(numero_normalise="+22670000001", prenom="  ", champs={})
    plein = Contact(numero_normalise="+22670000001", prenom="Awa", champs={"code": "C7"})
    assert gabarit.rendre(vide) == "Bonjour cher client, votre code à venir."
    assert gabarit.rendre(plein) == "Bonjour Awa, votre code C7."
    # Une valeur écrite dans la balise l'emporte sur celle du champ déclaré.
    assert compiler("{code|aucun}", {"code": "à venir"}).rendre(vide) == "aucun"


def test_aucun_message_rendu_ne_contient_de_virgule_orpheline():
    gabarit = compiler(
        "Bonjour {prenom|cher client}, {ville|votre ville}, votre code {code} vous attend, "
        "{prenom|ami}.",
        {"code": "à venir"},
    )
    hasard = random.Random(7)
    for _ in range(2000):
        contact = Contact(
            numero_normalise="+22670000001",
            prenom=hasard.choice(["", " ", "Awa", "Moussa"]),
            ville=hasard.choice(["", "Bobo"]),
            champs=hasard.choice([{}, {"code": ""}, {"code": "C1"}]),
        )
        rendu = gabarit.rendre(contact)
        assert not re.search(r"(^|\s),|,\s*,|\s{2,}", rendu), rendu


def test_un_modele_s_enregistre_avec_ses_variables(client):
    champs.declarer(client=client, libelle="Code client", valeur_repli="à venir")
    modele = modeles.enregistrer(
        client=client, nom="Relance", texte="Bonjour {prenom|cher client}, code {code_client}."
    )
    assert modele.variables == ["prenom", "code_client"]
    with pytest.raises(ModeleInvalide, match="existe déjà"):
        modeles.enregistrer(client=client, nom="Relance", texte="Autre texte")
    with pytest.raises(ModeleInvalide, match="demande une valeur de repli"):
        modeles.enregistrer(client=client, nom="Relance", texte="{nom}", modele=modele)


# Compteur au cas le plus long


@override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20)
def test_le_compteur_annonce_le_cas_le_plus_long(client):
    gabarit = compiler("Bonjour {nom|client}, " + "x" * 125, {})
    contacts = _contacts(client, [{"nom": "Ali"}, {"nom": "Abdoul-Aziz Ouédraogo Kaboré Zongo"}])
    courts, longs = (analyser(gabarit.rendre(c)) for c in contacts.order_by("numero_normalise"))
    assert (courts.segments, longs.segments) == (1, 2)
    estimation = estimer(client, gabarit, contacts)
    assert (estimation.destinataires, estimation.segments) == (2, 2)
    assert estimation.cout == 2 * 2 * 20
    assert "Abdoul-Aziz" in estimation.exemple


def test_un_seul_caractere_hors_gsm_fait_annoncer_l_ucs2(client):
    gabarit = compiler("Bonjour {prenom|ami}", {})
    contacts = _contacts(client, [{"prenom": "Awa"}, {"prenom": "Fâtima"}])
    assert estimer(client, gabarit, contacts).encodage == Encodage.UCS2


@override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=25)
def test_le_cout_annonce_n_est_jamais_inferieur_au_cout_reel_sur_mille_destinataires(client):
    hasard = random.Random(11)
    noms = ["", "Ali", "Ouédraogo", "Abdoul-Aziz Ouédraogo Kaboré", "Fâtima"]
    champs.declarer(client=client, libelle="Offre", valeur_repli="notre offre")
    lignes = [
        {
            "nom": hasard.choice(noms),
            "champs": {"offre": "y" * hasard.randint(0, 90)},
        }
        for _ in range(1000)
    ]
    contacts = _contacts(client, lignes)
    gabarit = modeles.gabarit(client, "Cher {nom|client}, profitez de {offre} avant dimanche.")
    estimation = estimer(client, gabarit, contacts)
    reel = sum(analyser(gabarit.rendre(c)).segments * 25 for c in contacts)
    assert estimation.destinataires == 1000
    assert estimation.cout >= reel
    print(f"annoncé {estimation.cout} FCFA, réel {reel} FCFA")
