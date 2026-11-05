"""Point de contrôle du lot 5 (T-68) : une campagne complète contre le simulateur.

Une campagne sur trois réseaux, avec personnalisation : la
composition, l'aperçu, l'envoi de test, la confirmation, la pause et la reprise
passent par les écrans du portail, sous le rôle applicatif. La publication, la
soumission et les accusés passent par les mêmes fonctions que l'ordonnanceur et
le worker d'envoi, sous le rôle des workers, contre des connecteurs simulés.

La soumission est menée message par message, dans l'ordre de la file, par la
fonction de soumission du worker : la pause tombe à un instant exact, et chaque
relivraison est rejouée volontairement. Le débit et le vrai RabbitMQ ont leur
propre test (T-63).

Le test ne lit le journal des messages que pour vérifier, borné à la campagne.

Sa taille se règle par `SMS_TAILLE_POINT_DE_CONTROLE`. La suite courante le
lance à mille destinataires : sur un poste de développement, chaque message
coûte de l'ordre de cent millisecondes de requêtes, et quarante mille prennent
environ une heure. Le passage à quarante mille se lance seul, à la demande
(décision de l'utilisateur du 5 novembre 2026) :

    SMS_TAILLE_POINT_DE_CONTROLE=40000 uv run pytest tests/test_point_de_controle_lot5.py
"""

import os
import re
import time
from collections import Counter

import pytest
from django.db import connections
from django.db.models import Count, Sum
from django.test import Client as Navigateur
from django.test import override_settings
from django.utils import timezone

from apps.campagnes import diffusion
from apps.campagnes.models import Campagne
from apps.comptes.models import Client, Utilisateur
from apps.comptes.services import creer_client, creer_utilisateur
from apps.contacts import liste_noire
from apps.contacts.models import Liste
from apps.core.etats import Etat
from apps.core.models import EvenementSortant
from apps.credits.models import CompteCredit, Ecriture, Reservation
from apps.journal.models import Compteur, Evenement, Message
from apps.journal.services import appliquer_accuse
from apps.telecom.models import Connecteur, Prefixe
from workers.envoi import WorkerEnvoi

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

TAILLE = int(os.environ.get("SMS_TAILLE_POINT_DE_CONTROLE", "1000"))
# Une part de la file reçue pendant la pause, relivrée, ou dont l'accusé est rejoué.
PART = max(TAILLE // 20, 10)
PRIX = 25
ACHAT = 5_000_000
PREFIXES = {"70": "ORANGE", "60": "MOOV", "58": "TELECEL"}
# Un prénom court et la valeur de repli tiennent en un segment, le prénom long
# en fait deux. Le coût annoncé suit le cas le plus long.
TEXTE = (
    "Bonjour {prenom|cher client}, votre cooperative vous informe que la "
    "distribution des semences aura lieu samedi au magasin central de Bobo. "
    "Apportez votre carte."
)
LONG = "Marie-Josephine-Kadidiatou Ouedraogo"
BLOQUES = ("+22670000003", "+22660000004", "+22658000005")


@pytest.fixture(autouse=True)
def reglages():
    with override_settings(SMS_PRIX_SEGMENT_PROVISOIRE=PRIX, SMS_NUMERO_COURT="36000"):
        yield


class Chrono:
    def __init__(self):
        self.debut = time.monotonic()
        self.phases = []

    def __call__(self, phase):
        maintenant = time.monotonic()
        self.phases.append(f"{phase} {maintenant - self.debut:.1f} s")
        print(self.phases[-1], flush=True)
        self.debut = maintenant


@pytest.fixture
def membre():
    compte = creer_client(
        raison_sociale="Cooperative des semences SA",
        forme=Client.Forme.SOCIETE,
        palier=Client.Palier.VERIFIE,
    )
    worker = connections["worker"]
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
            parametres={"taux_echec": 0.08, "graine": rang},
        )
    CompteCredit.objects.using("worker").create(client_id=compte.id, solde_acquis=ACHAT)
    Ecriture.objects.using("worker").create(
        client_id=compte.id, type="ACHAT", montant=ACHAT, solde_apres=ACHAT, reference="OM-1"
    )
    liste = Liste.objects.using("worker").create(
        client_id=compte.id, nom="Producteurs", nombre_contacts=TAILLE
    )
    prefixes = list(PREFIXES)
    with worker.cursor() as curseur:
        # Un prénom sur dix est vide (valeur de repli), un sur mille est long.
        curseur.execute(
            """
            INSERT INTO contacts_contact (
                id, client_id, cree_le, modifie_le, numero_normalise, numero_origine,
                operateur_presume, operateur_confirme, nom, prenom, ville, champs, origine,
                consentement_source, actif
            )
            SELECT gen_random_uuid(), %(client)s, now(), now(),
                '+226' || (%(prefixes)s::text[])[g %% 3 + 1] || lpad(g::text, 6, '0'), '',
                (%(operateurs)s::text[])[g %% 3 + 1], '', '',
                CASE WHEN g %% 10 = 0 THEN '' WHEN g %% 1000 = 7 THEN %(long)s ELSE 'Awa' END,
                '', '{}', 'SAISIE', '', true
            FROM generate_series(1, %(taille)s) AS g
            """,
            {
                "client": compte.id,
                "prefixes": prefixes,
                "operateurs": [PREFIXES[p] for p in prefixes],
                "long": LONG,
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
        curseur.execute("ANALYZE contacts_contact")
        curseur.execute("ANALYZE contacts_liste_contact")
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


def _file(campagne_id, depuis=0) -> list[dict]:
    """Les messages de file publiés pour la campagne, dans l'ordre."""
    publies = (
        EvenementSortant.objects.using("worker")
        .filter(nom__contains="envoi.")
        .order_by("id")
        .values_list("donnees", flat=True)
    )
    ids = {
        str(i)
        for i in Message.objects.using("worker")
        .filter(campagne_id=campagne_id)
        .values_list("id", flat=True)
    }
    return [d for d in publies if d.get("message_id") in ids][depuis:]


def _soumis(campagne_id) -> int:
    """Événements SOUMIS des messages de la campagne."""
    ids = Message.objects.using("worker").filter(campagne_id=campagne_id).values("id")
    return Evenement.objects.using("worker").filter(message_id__in=ids, etat=Etat.SOUMIS).count()


def _suivi(navigateur, campagne_id) -> dict:
    """Les compteurs lus dans le fragment, tels que les voit le client."""
    page = navigateur.get(f"/campagnes/{campagne_id}/fragments/suivi").content.decode()
    valeurs = re.findall(r'<dt class="text-slate-500">([^<]+)</dt><dd class="text-lg">(\d+)', page)
    return {libelle: int(n) for libelle, n in valeurs} | {"en_direct": "hx-get" in page}


def _avec_role(espaces, role, fonction, *args):
    espaces(role)
    try:
        return fonction(*args)
    finally:
        espaces("client")


def test_une_campagne_complete_de_bout_en_bout(membre, espaces):
    chrono = Chrono()
    navigateur = Navigateur()
    navigateur.force_login(membre)
    liste = Liste.objects.using("worker").get()
    chrono("contacts")

    # Composition, aperçu, test et confirmation : par les écrans.
    reponse = navigateur.post(
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
    assert reponse["Location"] == f"/campagnes/{campagne.id}"
    page = navigateur.get(f"/campagnes/{campagne.id}").content.decode()
    lus = re.search(r"Envoyer à (\d+) destinataires pour (\d+) FCFA", page)
    destinataires, annonce = int(lus[1]), int(lus[2])
    assert destinataires == TAILLE
    assert annonce == TAILLE * 2 * PRIX  # le cas le plus long : deux segments
    assert f"Bonjour {LONG}," in page  # l'aperçu montre le cas le plus long
    chrono("apercu")
    page_test = navigateur.post(
        f"/campagnes/{campagne.id}/test", {"numero": "70 99 88 77"}, follow=True
    ).content.decode()
    assert "Test envoyé à +22670998877" in page_test
    page = navigateur.post(
        f"/campagnes/{campagne.id}/confirmer",
        {"destinataires": str(destinataires), "cout": str(annonce)},
        follow=True,
    ).content.decode()
    assert f"{annonce} FCFA réservés" in page

    # Trois numéros passent sur liste noire après la confirmation : refusés sans frais.
    def bloquer():
        for numero in BLOQUES:
            liste_noire.bloquer(client=membre.client, numero=numero, origine="SAISIE")

    _avec_role(espaces, "worker", bloquer)
    chrono("confirmation")

    # Publication par l'ordonnanceur.
    def publier_tout():
        while diffusion.publier():
            pass

    _avec_role(espaces, "worker", publier_tout)
    file = _file(campagne.id)
    assert len(file) == TAILLE
    chrono("publication")

    # Le message de test part par son propre worker, hors campagne.
    essai = Message.objects.using("worker").get(campagne_id__isnull=True)
    worker_essai = WorkerEnvoi(files=[])
    _avec_role(
        espaces,
        "worker",
        worker_essai._soumettre,
        {"message_id": str(essai.id), "date_reception": essai.date_reception.isoformat()},
    )

    # Un premier tiers part, le suivi avance sans jamais reculer.
    worker = WorkerEnvoi(files=[])
    tiers = TAILLE // 3
    releves = [_suivi(navigateur, campagne.id)]

    def soumettre(donnees_list):
        for donnees in donnees_list:
            worker._soumettre(donnees)

    for debut in range(0, tiers, tiers // 4 + 1):
        _avec_role(espaces, "worker", soumettre, file[debut : min(debut + tiers // 4 + 1, tiers)])
        releves.append(_suivi(navigateur, campagne.id))
    partis = [r["Partis vers le réseau"] for r in releves]
    assert partis == sorted(partis) and partis[-1] > 0
    chrono("premier tiers")

    # Pause par l'écran : ce que le worker reçoit ensuite est retenu, pas soumis.
    page = navigateur.post(f"/campagnes/{campagne.id}/pause", follow=True).content.decode()
    assert "en pause" in page
    soumis_a_la_pause = _soumis(campagne.id)
    retenus = file[tiers : tiers + 2 * PART]
    _avec_role(espaces, "worker", soumettre, retenus)
    assert _soumis(campagne.id) == soumis_a_la_pause
    ids_retenus = [d["message_id"] for d in retenus]
    assert set(
        Message.objects.using("worker")
        .filter(campagne_id=campagne.id, id__in=ids_retenus)
        .values_list("etat", flat=True)
    ) == {Etat.EN_ATTENTE}
    _avec_role(espaces, "worker", publier_tout)  # en pause : rien ne se publie
    assert len(_file(campagne.id)) == TAILLE
    assert _suivi(navigateur, campagne.id)["Partis vers le réseau"] == partis[-1]
    chrono("pause")

    # Reprise par l'écran : l'ordonnanceur remet les retenus en file sous de
    # nouvelles clés ; le worker reçoit le reste, les remises, et des relivraisons.
    navigateur.post(f"/campagnes/{campagne.id}/reprendre")
    _avec_role(espaces, "worker", publier_tout)
    remises = _file(campagne.id, depuis=TAILLE)
    assert sorted(d["message_id"] for d in remises) == sorted(ids_retenus)
    _avec_role(espaces, "worker", soumettre, file[tiers:] + remises + file[:PART] + retenus[:PART])
    chrono("reprise et fin de soumission")

    # Les accusés du simulateur, puis un rejeu d'une partie d'entre eux.
    accuses = [
        accuse
        for passerelle in worker.passerelles.values()
        for accuse in passerelle.accuses_dus(timezone.now() + timezone.timedelta(hours=1))
    ]

    def appliquer(liste_accuses):
        for accuse in liste_accuses:
            appliquer_accuse(accuse)

    _avec_role(espaces, "worker", appliquer, accuses + accuses[:PART])
    _avec_role(espaces, "worker", publier_tout)  # clôture
    chrono("accuses et cloture")

    # Le rapport final, tel que le client le lit.
    campagne = Campagne.objects.using("worker").get()
    assert campagne.etat == Campagne.Etat.TERMINEE and campagne.terminee_le
    page = navigateur.get(f"/campagnes/{campagne.id}").content.decode()
    assert f"coût réel {campagne.cout_reel} FCFA" in page
    rapport = _suivi(navigateur, campagne.id)
    assert rapport["en_direct"] is False

    # Le journal, compté une seule fois ici, borné à la campagne.
    messages = Message.objects.using("worker").filter(campagne_id=campagne.id)
    etats = dict(messages.values_list("etat").annotate(n=Count("id")).values_list("etat", "n"))
    assert messages.count() == TAILLE
    assert messages.values("contact_id").distinct().count() == TAILLE  # aucun doublon
    assert etats.get(Etat.REJETE) == len(BLOQUES)
    livres, echecs = etats.get(Etat.LIVRE, 0), etats.get(Etat.NON_LIVRE, 0)
    partis_reels = TAILLE - len(BLOQUES)
    assert livres + echecs == partis_reels and echecs > 0
    assert rapport["Partis vers le réseau"] == partis_reels
    assert (rapport["Livrés"], rapport["Non livrés"]) == (livres, echecs)
    assert rapport["Refusés avant le réseau"] == len(BLOQUES)
    assert rapport["Non soumis"] == 0 and rapport["Annulés"] == 0

    # Une seule soumission par message, au journal comme au simulateur.
    assert _soumis(campagne.id) == partis_reels
    recus = Counter(
        s.numero
        for passerelle in worker.passerelles.values()
        for s in passerelle.soumissions.values()
    )
    assert len(recus) == partis_reels and max(recus.values()) == 1

    # Le coût : réel = consommé = somme des messages partis, au plus l'annoncé.
    cout_messages = messages.filter(date_soumission__isnull=False).aggregate(
        total=Sum("cout_total")
    )["total"]
    consomme = (
        -Ecriture.objects.using("worker")
        .filter(
            client_id=campagne.client_id,
            type=Ecriture.Type.CONSOMMATION,
            reference__in=[f"message:{i}" for i in messages.values_list("id", flat=True)],
        )
        .aggregate(total=Sum("montant"))["total"]
    )
    assert campagne.cout_reel == consomme == cout_messages
    assert campagne.cout_reel < annonce  # les prénoms courts tiennent en un segment
    reservation = Reservation.objects.using("worker").get(campagne_id=campagne.id)
    assert reservation.etat != Reservation.Etat.ACTIVE
    compte = CompteCredit.objects.using("worker").get(client_id=campagne.client_id)
    assert compte.montant_reserve == 0
    solde = (
        Ecriture.objects.using("worker")
        .filter(client_id=campagne.client_id)
        .aggregate(total=Sum("montant"))["total"]
    )
    essai.refresh_from_db()
    assert f"facturé {essai.cout_total} FCFA" in page_test  # ce que le client a lu
    assert solde == ACHAT - campagne.cout_reel - essai.cout_total  # et le test, à part

    # Les compteurs du suivi égalent le journal.
    compteurs = Compteur.objects.using("worker").filter(campagne_id=campagne.id)
    totaux = compteurs.aggregate(
        emis=Sum("emis"), livres=Sum("livres"), echecs=Sum("echecs"), refuses=Sum("refuses")
    )
    assert totaux == {
        "emis": partis_reels,
        "livres": livres,
        "echecs": echecs,
        "refuses": len(BLOQUES),
    }
    chrono("verifications")
    print(" ; ".join(chrono.phases))
