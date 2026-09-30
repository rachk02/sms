"""Découpage d'une campagne et publication en file (T-62).

Sous le rôle du worker, comme l'ordonnanceur ; préfixes fictifs.
"""

import io
from collections import Counter

import pytest
from django.db import IntegrityError
from django.test import override_settings
from django.utils import timezone

from apps.campagnes import diffusion, services
from apps.campagnes.models import Campagne
from apps.comptes.models import Client
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts import champs, listes
from apps.contacts.models import ChampPersonnalise, Contact
from apps.contacts.services import creer_contact
from apps.core.models import EvenementSortant
from apps.credits import services as credits
from apps.expediteurs import services as expediteurs
from apps.expediteurs.models import Expediteur
from apps.journal.models import Message
from apps.journal.services import enregistrer_message
from apps.telecom.models import Prefixe

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("reglages"),
]

PREFIXES = {"70": "ORANGE", "60": "MOOV", "58": "TELECEL"}


@pytest.fixture
def reglages():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=20, SMS_NUMERO_COURT="36000"):
        yield


@pytest.fixture
def client(en_tant_que_worker):
    Prefixe.objects.bulk_create(
        Prefixe(pays="BF", prefixe=p, operateur=o) for p, o in PREFIXES.items()
    )
    client = creer_client(
        raison_sociale="Faso Riz SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )
    credits.acheter(client.id, 1_000_000, reference="OM-1")
    yield client
    Prefixe.objects.filter(pays="BF").delete()


@pytest.fixture
def agent(client):
    return creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x", nom="Agent")


def _approuver(expediteur, operateur, agent):
    expediteurs.deposer_dossier(expediteur.id, operateur, agent=agent, reference=f"D-{operateur}")
    expediteurs.decider(
        expediteur.id,
        operateur,
        agent=agent,
        approuve=True,
        decide_le=timezone.now(),
        reference=f"D-{operateur}",
    )


@pytest.fixture
def fasoriz(client, agent):
    expediteur = expediteurs.declarer(
        client=client,
        libelle="FASORIZ",
        mandat=io.BytesIO(b"%PDF-1.7 mandat"),
        nom_mandat="mandat.pdf",
        auteur=None,
    )
    _approuver(expediteur, "ORANGE", agent)
    return expediteur


def _liste(client, repartition: dict) -> list:
    liste = listes.creer(client=client, nom=f"Liste {timezone.now().timestamp()}")
    ids = []
    for prefixe, nombre in repartition.items():
        for n in range(nombre):
            ids.append(
                creer_contact(
                    client=client, numero=f"{prefixe}{n:06d}", origine="SAISIE", prenom=f"P{n}"
                ).id
            )
    listes.ajouter(liste, ids)
    return [liste.id]


def _confirmee(client, cible, *, expediteur=None, politique="REPLI", type_="MARKETING", texte=None):
    campagne = services.creer(
        client=client,
        auteur=None,
        nom="Promo",
        texte=texte or "Bonjour {prenom|cher client}, promotion.",
        listes=cible,
        expediteur_id=expediteur.id if expediteur else None,
        politique=politique,
        type_=type_,
    )
    vu = services.apercu(campagne)
    return services.confirmer(
        campagne, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )


def _files(campagne) -> Counter:
    noms = EvenementSortant.objects.filter(
        cle_idempotence__in=[
            f"envoi:{i}"
            for i in Message.objects.filter(campagne_id=campagne.id).values_list("id", flat=True)
        ]
    ).values_list("nom", flat=True)
    return Counter(nom.split("envoi.", 1)[1] for nom in noms)


def test_un_lot_melange_se_repartit_dans_les_trois_files_de_son_reseau(client):
    campagne = _confirmee(client, _liste(client, {"70": 6, "60": 4, "58": 3}))
    assert diffusion.publier() == 13
    assert _files(campagne) == {"masse.orange": 6, "masse.moov": 4, "masse.telecel": 3}
    campagne.refresh_from_db()
    assert campagne.etat == Campagne.Etat.EN_COURS and campagne.demarree_le


def test_une_campagne_transactionnelle_passe_par_les_files_prioritaires(client):
    campagne = _confirmee(client, _liste(client, {"70": 2, "60": 1}), type_="TRANSACTIONNEL")
    diffusion.publier()
    assert _files(campagne) == {"prioritaire.orange": 2, "prioritaire.moov": 1}


def test_une_campagne_rejouee_entierement_ne_produit_aucun_doublon(client):
    campagne = _confirmee(client, _liste(client, {"70": 5, "60": 5}))
    assert diffusion.publier_lot(campagne.id, taille=4) == 4
    assert diffusion.publier() == 6
    assert diffusion.publier() == 0  # rejouée : tout est déjà servi
    messages = Message.objects.filter(campagne_id=campagne.id)
    assert messages.count() == 10 == len(set(messages.values_list("contact_id", flat=True)))
    # Même forcée à la main, une seconde écriture pour un contact servi est bloquée.
    deja = messages.first()
    with pytest.raises(IntegrityError):
        enregistrer_message(
            client=client,
            numero_normalise=deja.numero_normalise,
            operateur=deja.operateur,
            expediteur_libelle="36000",
            texte="doublon",
            cout_unitaire=20,
            campagne_id=campagne.id,
            contact_id=deja.contact_id,
        )


def test_le_texte_est_personnalise_et_l_expediteur_suit_le_reseau(client, fasoriz):
    campagne = _confirmee(client, _liste(client, {"70": 2, "60": 2}), expediteur=fasoriz)
    diffusion.publier()
    envoyes = dict(
        Message.objects.filter(campagne_id=campagne.id).values_list(
            "numero_normalise", "expediteur_libelle"
        )
    )
    assert envoyes == {
        "+22670000000": "FASORIZ",
        "+22670000001": "FASORIZ",
        "+22660000000": "36000",
        "+22660000001": "36000",
    }
    textes = set(Message.objects.filter(campagne_id=campagne.id).values_list("texte", flat=True))
    assert textes == {"Bonjour P0, promotion.", "Bonjour P1, promotion."}


def test_un_identifiant_suspendu_pendant_la_diffusion_bascule_sur_le_numero_court(client, fasoriz):
    campagne = _confirmee(client, _liste(client, {"70": 4}), expediteur=fasoriz)
    diffusion.publier_lot(campagne.id, taille=2)
    Expediteur.objects.filter(id=fasoriz.id).update(etat=Expediteur.Etat.SUSPENDU)
    diffusion.publier()
    expediteurs_ = Counter(
        Message.objects.filter(campagne_id=campagne.id).values_list("expediteur_libelle", flat=True)
    )
    assert expediteurs_ == {"FASORIZ": 2, "36000": 2}


def test_les_destinataires_en_attente_partent_a_l_approbation(client, fasoriz, agent):
    campagne = _confirmee(
        client, _liste(client, {"70": 2, "60": 3}), expediteur=fasoriz, politique="ATTENDRE"
    )
    assert diffusion.publier() == 2  # Moov attend
    assert diffusion.publier() == 0
    _approuver(fasoriz, "MOOV", agent)
    assert diffusion.publier() == 3
    moov = Message.objects.filter(campagne_id=campagne.id, operateur="MOOV")
    assert set(moov.values_list("expediteur_libelle", flat=True)) == {"FASORIZ"}


def test_un_champ_supprime_apres_la_confirmation_n_arrete_pas_la_diffusion(client):
    champs.declarer(client=client, libelle="Code", valeur_repli="à venir")
    Contact.objects.filter(client=client).update(champs={})
    campagne = _confirmee(
        client, _liste(client, {"70": 2}), texte="Votre code : {code}. Merci {prenom|ami}."
    )
    champs.supprimer(ChampPersonnalise.objects.get(client=client))
    assert diffusion.publier() == 2
    textes = set(Message.objects.filter(campagne_id=campagne.id).values_list("texte", flat=True))
    assert textes == {"Votre code : à venir. Merci P0.", "Votre code : à venir. Merci P1."}


def test_un_contact_au_reseau_inconnu_n_est_ni_fige_ni_facture(client):
    cible = _liste(client, {"70": 2})
    inconnu = creer_contact(client=client, numero="55000001", origine="SAISIE")
    listes.ajouter(listes.creer(client=client, nom="Autre"), [inconnu.id])
    campagne = services.creer(
        client=client,
        auteur=None,
        nom="Promo",
        texte="Promotion",
        listes=[*cible, *listes.Liste.objects.filter(nom="Autre").values_list("id", flat=True)],
    )
    vu = services.apercu(campagne)
    assert (vu.destinataires, vu.inconnus, vu.cout) == (2, 1, 40)
