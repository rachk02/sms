"""Point de contrôle du lot 7 (T-90) : de l'argent au SMS, et retour à la facture.

Un client recharge par le fournisseur simulé de mobile money : le formulaire,
la page du fournisseur et la page d'attente passent par le portail, sous le
rôle applicatif ; le rappel signé du fournisseur arrive par le point d'entrée
public, et sa vérification par la fonction du worker paiements. Le client, en
paiement à la livraison, lance ensuite une campagne par les écrans : les échecs
de livraison et les numéros passés sur liste noire libèrent leur réservation.
La facture de l'achat est émise par le balayage de l'ordonnanceur, et le
rapprochement du registre ne trouve aucun écart. Le rappel de paiement rejoué
ne crédite pas une seconde fois.

Le simulateur de connecteurs échoue sur une part des messages ; la taille reste
petite, le débit et la volumétrie ont leurs propres tests.
"""

import json
import re

import pytest
from django.core import mail
from django.db import connections
from django.db.models import Sum
from django.test import Client as Navigateur
from django.test import override_settings
from django.utils import timezone

from apps.campagnes import diffusion
from apps.campagnes.models import Campagne
from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import changer_mode_reglement, creer_client, creer_utilisateur
from apps.contacts import liste_noire
from apps.contacts.models import Liste
from apps.core.etats import Etat
from apps.core.models import EvenementSortant
from apps.core.redis import client_redis
from apps.credits import rapprochement
from apps.credits.models import CompteCredit, Ecriture, Reservation
from apps.journal.models import Message
from apps.journal.services import appliquer_accuse
from apps.paiements import services as paiements
from apps.paiements.fournisseurs import ENTETE_SIGNATURE, signer
from apps.paiements.models import Facture, Transaction
from apps.telecom.models import Connecteur, Prefixe
from workers.envoi import WorkerEnvoi
from workers.paiements import _verifier

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

TAILLE = 60
PRIX = 25
CREDITS, TTC = 10_000, 11_800
SECRET = "secret-du-point-de-controle"
PREFIXES = {"70": "ORANGE", "60": "MOOV", "58": "TELECEL"}
TEXTE = "Bonjour {prenom|cher client}, les semences arrivent samedi au magasin."
BLOQUES = ("+22670000003", "+22660000004")
EMETTEUR = {
    "raison_sociale": "Émetteur d'essai SARL",
    "rccm": "BF-OUA-ESSAI-B-0001",
    "ifu": "00000000X",
    "regime_fiscal": "Régime d'essai",
    "adresse": "01 BP 0000 Ouagadougou 01",
}


@pytest.fixture(autouse=True)
def reglages():
    def vider():
        redis = client_redis()
        for motif in ("sms:paiements:simule:*", "sms:paiements:rappels:*"):
            for cle in redis.scan_iter(motif):
                redis.delete(cle)

    vider()
    with override_settings(
        SMS_PRIX_SEGMENT_PROVISOIRE=PRIX,
        SMS_NUMERO_COURT="36000",
        PAIEMENTS_SIMULATEUR=True,
        PAIEMENTS_SIMULE_SECRET=SECRET,
        PAIEMENTS_PLAGES={"simule": ["10.0.0.0/24"]},
        PROXIES_DE_CONFIANCE=[],
        FACTURATION_EMETTEUR=EMETTEUR,
    ):
        yield
    vider()


@pytest.fixture
def membre():
    compte = creer_client(
        raison_sociale="Cooperative des semences SA",
        forme=Client.Forme.SOCIETE,
        palier=Client.Palier.VERIFIE,
    )
    Prefixe.objects.using("worker").bulk_create(
        Prefixe(pays="BF", prefixe=prefixe, operateur=operateur)
        for prefixe, operateur in PREFIXES.items()
    )
    for rang, operateur in enumerate(PREFIXES.values()):
        Connecteur.objects.using("worker").create(
            nom=f"simulateur-{operateur.lower()}",
            operateur=operateur,
            type=Connecteur.Type.SIMULE,
            etat_liaison=Connecteur.EtatLiaison.CONNECTE,
            debit_max=100_000,
            parametres={"taux_echec": 0.2, "graine": rang},
        )
    liste = Liste.objects.using("worker").create(
        client_id=compte.id, nom="Producteurs", nombre_contacts=TAILLE
    )
    prefixes = list(PREFIXES)
    with connections["worker"].cursor() as curseur:
        curseur.execute(
            """
            INSERT INTO contacts_contact (
                id, client_id, cree_le, modifie_le, numero_normalise, numero_origine,
                operateur_presume, operateur_confirme, nom, prenom, ville, champs, origine,
                consentement_source, actif
            )
            SELECT gen_random_uuid(), %(client)s, now(), now(),
                '+226' || (%(prefixes)s::text[])[g %% 3 + 1] || lpad(g::text, 6, '0'), '',
                (%(operateurs)s::text[])[g %% 3 + 1], '', '', 'Awa', '', '{}', 'SAISIE', '', true
            FROM generate_series(1, %(taille)s) AS g
            """,
            {
                "client": compte.id,
                "prefixes": prefixes,
                "operateurs": [PREFIXES[p] for p in prefixes],
                "taille": TAILLE,
            },
        )
        curseur.execute(
            """
            INSERT INTO contacts_liste_contact (liste_id, contact_id, client_id, ajoute_le)
            SELECT %s, id, client_id, now() FROM contacts_contact WHERE client_id = %s
            """,
            [liste.id, compte.id],
        )
    utilisateur = creer_utilisateur(
        courriel="direction@semences.bf",
        mot_de_passe="x-1234-abcd",
        nom="Direction",
        client=compte,
        role=Utilisateur.Role.ADMIN,
        courriel_verifie_le=timezone.now(),
    )
    yield utilisateur
    Prefixe.objects.using("worker").filter(pays="BF").delete()


def _avec_role(espaces, role, fonction, *args, **kwargs):
    espaces(role)
    try:
        return fonction(*args, **kwargs)
    finally:
        espaces("client")


def _rappel(navigateur, reference):
    """Le rappel signé du fournisseur, par le point d'entrée public."""
    corps = json.dumps({"reference": reference, "statut": "CONFIRME"}).encode()
    entete = f"HTTP_{ENTETE_SIGNATURE.upper().replace('-', '_')}"
    return navigateur.post(
        "/rappels/paiements/simule",
        data=corps,
        content_type="application/json",
        REMOTE_ADDR="10.0.0.5",
        **{entete: signer(SECRET, corps)},
    )


def _rappels_deposes():
    return list(
        EvenementSortant.objects.using("worker")
        .filter(nom__endswith="paiements.rappels")
        .order_by("id")
        .values_list("donnees", flat=True)
    )


def _registre(client_id, **filtres):
    total = (
        Ecriture.objects.using("worker")
        .filter(client_id=client_id, **filtres)
        .aggregate(total=Sum("montant"))["total"]
    )
    return total or 0


def test_du_rechargement_a_la_facture(membre, espaces):
    client_id = membre.client_id
    navigateur = Navigateur()
    navigateur.force_login(membre)
    assert _registre(client_id) == 0  # solde initial

    # Rechargement par les écrans : formulaire, page du fournisseur, retour.
    reponse = navigateur.post(
        "/paiements/recharger", {"credits": str(CREDITS), "fournisseur": "simule"}
    )
    transaction_ = Transaction.objects.using("worker").get()
    assert transaction_.montant == TTC
    reponse = navigateur.post(f"{reponse['Location']}/choix", {"choix": "payer_sans_rappel"})
    attente = navigateur.get(reponse["Location"]).content.decode()
    assert "En attente de la confirmation" in attente

    # Le fournisseur rappelle ; le worker vérifie auprès de lui, puis crédite.
    assert _rappel(navigateur, transaction_.reference_transaction).status_code == 200
    (depose,) = _rappels_deposes()
    _avec_role(espaces, "worker", _verifier, json.dumps({"donnees": depose}).encode())
    assert _registre(client_id, type=Ecriture.Type.ACHAT) == CREDITS
    statut = navigateur.get(f"/paiements/{transaction_.id}/fragments/statut").content.decode()
    assert "Rechargement confirmé" in statut and "hx-get" not in statut
    assert any("Rechargement confirmé" in m.subject for m in mail.outbox)

    # Paiement à la livraison : un message non livré rend sa réservation.
    def a_la_livraison():
        changer_mode_reglement(
            Client.objects.get(id=client_id),
            Client.Reglement.LIVRAISON,
            auteur=Utilisateur.objects.get(id=membre.id),
            motif="Contrat grand compte",
        )

    _avec_role(espaces, "worker", a_la_livraison)

    # Une campagne par les écrans.
    liste = Liste.objects.using("worker").get()
    navigateur.post(
        "/campagnes/nouvelle",
        {
            "nom": "Semences de saison",
            "type": "MARKETING",
            "texte": TEXTE,
            "listes": [str(liste.id)],
            "politique": "REPLI",
        },
    )
    campagne = Campagne.objects.using("worker").get()
    page = navigateur.get(f"/campagnes/{campagne.id}").content.decode()
    lus = re.search(r"Envoyer à (\d+) destinataires pour (\d+) FCFA", page)
    destinataires, annonce = int(lus[1]), int(lus[2])
    assert (destinataires, annonce) == (TAILLE, TAILLE * PRIX)
    page = navigateur.post(
        f"/campagnes/{campagne.id}/confirmer",
        {"destinataires": str(destinataires), "cout": str(annonce)},
        follow=True,
    ).content.decode()
    assert f"{annonce} FCFA réservés" in page

    def bloquer():
        for numero in BLOQUES:
            liste_noire.bloquer(client=membre.client, numero=numero, origine="SAISIE")

    def publier_tout():
        while diffusion.publier():
            pass

    _avec_role(espaces, "worker", bloquer)
    _avec_role(espaces, "worker", publier_tout)
    file = [
        d
        for d in EvenementSortant.objects.using("worker")
        .filter(nom__contains="envoi.")
        .order_by("id")
        .values_list("donnees", flat=True)
    ]
    worker = WorkerEnvoi(files=[])

    def soumettre():
        for donnees in file:
            worker._soumettre(donnees)

    def accuser():
        echeance = timezone.now() + timezone.timedelta(hours=1)
        for passerelle in worker.passerelles.values():
            for accuse in passerelle.accuses_dus(echeance):
                appliquer_accuse(accuse)

    _avec_role(espaces, "worker", soumettre)
    _avec_role(espaces, "worker", accuser)
    _avec_role(espaces, "worker", publier_tout)  # clôture de la campagne

    campagne = Campagne.objects.using("worker").get()
    assert campagne.etat == Campagne.Etat.TERMINEE
    messages = Message.objects.using("worker").filter(campagne_id=campagne.id)
    livres = messages.filter(etat=Etat.LIVRE).count()
    non_livres = messages.filter(etat=Etat.NON_LIVRE).count()
    assert messages.filter(etat=Etat.REJETE).count() == len(BLOQUES)
    assert livres + non_livres == TAILLE - len(BLOQUES) and non_livres > 0
    # À la livraison : seuls les livrés consomment, les échecs ont été libérés.
    consomme = -_registre(client_id, type=Ecriture.Type.CONSOMMATION)
    assert consomme == campagne.cout_reel == livres * PRIX
    reservation = Reservation.objects.using("worker").get(campagne_id=campagne.id)
    assert reservation.etat != Reservation.Etat.ACTIVE

    # Solde final, au franc près : initial + achat - consommation réelle.
    compte = CompteCredit.objects.using("worker").get(client_id=client_id)
    assert _registre(client_id) == compte.solde_acquis == 0 + CREDITS - consomme
    assert compte.montant_reserve == 0

    # La facture de l'achat, par le balayage de l'ordonnanceur.
    assert _avec_role(espaces, "worker", paiements.facturer_en_attente) == 1
    facture = Facture.objects.using("worker").get()
    assert facture.transaction_id == transaction_.id
    assert (facture.montant_ht, facture.montant_ttc) == (CREDITS, TTC)
    assert facture.fichier_id

    # Le rappel rejoué ne crédite pas une seconde fois.
    assert _rappel(navigateur, transaction_.reference_transaction).status_code == 200
    rejoue = _rappels_deposes()[-1]
    _avec_role(espaces, "worker", _verifier, json.dumps({"donnees": rejoue}).encode())
    assert _registre(client_id, type=Ecriture.Type.ACHAT) == CREDITS
    assert _avec_role(espaces, "worker", paiements.facturer_en_attente) == 0

    # Le rapprochement du registre ne trouve aucun écart.
    assert _avec_role(espaces, "worker", rapprochement.rapprocher) == []
