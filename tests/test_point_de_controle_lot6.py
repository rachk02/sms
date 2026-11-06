# Les fixtures de test_pause sont importées : leurs noms reviennent en paramètres.
# ruff: noqa: F811

"""Point de contrôle du lot 6 (T-76).

Un scénario complet : une campagne part vers un numéro ; une seconde campagne
est confirmée, ses destinataires figés, ce numéro compris ; le numéro répond
par un STOP sous une forme inhabituelle, reçu par le point d'entrée des rappels
de la passerelle ; la désinscription est inscrite et confirmée ; la seconde
campagne part, et le message vers ce numéro est bloqué au filtrage avant
soumission, et non à la constitution de la campagne ; enfin le dossier de
preuve du numéro réunit l'origine du contact, sa date d'entrée, les messages
reçus et le message de désinscription d'origine.

Chaque événement publié par la boîte d'envoi est remis au consommateur que le
courtier servirait, deux fois quand c'est utile, pour le rejeu.
"""

import json

import pytest
from django.test import Client as Navigateur

from apps.backoffice import selectors as backoffice
from apps.campagnes import diffusion, services
from apps.campagnes.models import Campagne
from apps.campagnes.tests.test_pause import (  # noqa: F401  (fixtures)
    _confirmee,
    _en_file,
    client,
    reglages,
    worker,
)
from apps.contacts.liste_noire import DESINSCRIPTION_INSCRITE
from apps.contacts.models import Contact, Liste, ListeNoire
from apps.core.etats import Etat
from apps.core.models import EvenementSortant
from apps.journal.models import Message
from apps.journal.services import DESINSCRIPTION_RAPPROCHEE
from apps.telecom.models import Connecteur, MessageEntrant, MotCle
from apps.telecom.rappels import jeton
from apps.telecom.services import DESINSCRIPTION_RECUE
from workers.accuses import (
    _confirmer_desinscription,
    _desinscrire,
    _enregistrer_entrant,
    _rapprocher_desinscription,
)

pytestmark = [
    pytest.mark.django_db(transaction=True, databases=["default", "worker"]),
    pytest.mark.usefixtures("reglages"),
]

NUMERO = "+22670000001"
STOP_INHABITUEL = "  S.T.O.P  merci !"
CONFIRMATION = "Vous ne recevrez plus de SMS de {expediteur}. Votre désinscription est enregistrée."


def _livre(sortant):
    """Un événement de la boîte d'envoi, tel que le courtier le livre."""

    class Livre:
        message_id = sortant.cle_idempotence
        body = json.dumps(
            {
                "nom": sortant.nom,
                "client_id": str(sortant.client_id) if sortant.client_id else None,
                "objet_id": str(sortant.objet_id) if sortant.objet_id else None,
                "donnees": sortant.donnees,
                "cle_idempotence": sortant.cle_idempotence,
            }
        ).encode()

    return Livre


def _livrer(nom, consommateur):
    for sortant in EvenementSortant.objects.filter(nom__endswith=nom).order_by("cree_le", "id"):
        consommateur(_livre(sortant))
        consommateur(_livre(sortant))  # relivré par le courtier


def _soumettre_tout(worker, campagne, depuis=0):
    for donnees in _en_file(campagne, depuis=depuis):
        worker._soumettre(donnees)


def test_du_stop_inhabituel_au_dossier_de_preuve(client, worker):
    MotCle.objects.get_or_create(
        mot_cle="STOP",
        action=MotCle.Action.DESINSCRIPTION,
        defaults={"reponse_automatique": CONFIRMATION},
    )
    connecteur = Connecteur.objects.get(operateur="ORANGE")

    # 1. Une première campagne atteint le numéro.
    premiere = _confirmee(client, 3)
    diffusion.publier()
    _soumettre_tout(worker, premiere)
    assert Message.objects.get(campagne_id=premiere.id, numero_normalise=NUMERO).etat == (
        Etat.SOUMIS
    )

    # 2. Une seconde campagne est confirmée : ses destinataires sont figés,
    # le numéro compris, avant toute désinscription.
    seconde = services.creer(
        client=client,
        auteur=None,
        nom="Relance",
        texte="Bonjour {prenom|cher client}, dernière chance.",
        listes=[Liste.objects.get(client=client, nom="Clients").id],
    )
    vu = services.apercu(seconde)
    seconde = services.confirmer(
        seconde, auteur=None, destinataires_lus=vu.destinataires, cout_lu=vu.cout
    )
    assert seconde.destinataires_prevus == 3

    # 3. Le numéro répond STOP, sous une forme inhabituelle, par la passerelle.
    reponse = Navigateur().post(
        f"/rappels/jasmin/{connecteur.id}?jeton={jeton(connecteur.id)}",
        {"id": "mo-76", "from": "22670000001", "to": "36000", "content": STOP_INHABITUEL},
    )
    assert reponse.status_code == 200
    for sortant in EvenementSortant.objects.filter(nom__endswith="messages.entrants"):
        _enregistrer_entrant(_livre(sortant).body)
        _enregistrer_entrant(_livre(sortant).body)
    entrant = MessageEntrant.objects.get()
    assert (entrant.texte, entrant.mot_cle_reconnu) == (STOP_INHABITUEL, "STOP")

    _livrer(DESINSCRIPTION_RECUE, _rapprocher_desinscription)
    _livrer(DESINSCRIPTION_RAPPROCHEE, _desinscrire)
    _livrer(DESINSCRIPTION_INSCRITE, _confirmer_desinscription)

    # 4. La désinscription est effective et confirmée, gratuitement.
    inscription = ListeNoire.objects.get(client=client, numero_normalise=NUMERO)
    assert inscription.message_origine == STOP_INHABITUEL
    confirmation = Message.objects.get(nature=Message.Nature.SYSTEME)
    worker._soumettre(
        {
            "message_id": str(confirmation.id),
            "date_reception": confirmation.date_reception.isoformat(),
        }
    )
    confirmation.refresh_from_db()
    assert confirmation.etat == Etat.SOUMIS and confirmation.cout_total == 0

    # 5. La seconde campagne part : le numéro est bloqué au filtrage avant
    # soumission, les deux autres destinataires reçoivent leur message.
    diffusion.publier()
    _soumettre_tout(worker, seconde)
    bloque = Message.objects.get(campagne_id=seconde.id, numero_normalise=NUMERO)
    assert (bloque.etat, bloque.motif_echec) == (Etat.REJETE, "LISTE_NOIRE")
    assert bloque.date_soumission is None
    autres = Message.objects.filter(campagne_id=seconde.id).exclude(numero_normalise=NUMERO)
    assert {m.etat for m in autres} == {Etat.SOUMIS}
    diffusion.publier()  # clôture
    seconde.refresh_from_db()
    assert seconde.etat == Campagne.Etat.TERMINEE

    # 6. Le dossier de preuve réunit tout ce qu'un agent doit répondre.
    dossier = backoffice.dossier(NUMERO)
    (ligne,) = [ligne for ligne in dossier.clients if ligne.client == client]
    contact = Contact.objects.get(client=client, numero_normalise=NUMERO)
    assert ligne.contact == contact and ligne.contact.origine == Contact.Origine.SAISIE
    assert ligne.contact.cree_le is not None  # la date d'entrée
    assert ligne.messages == 1  # parti vers le réseau ; le second a été bloqué
    assert ligne.liste_noire.message_origine == STOP_INHABITUEL
    assert [e.texte for e in dossier.desinscriptions] == [STOP_INHABITUEL]
    recus = backoffice.messages_recus(NUMERO, client.id)
    assert {(m.campagne_id, m.etat) for m in recus if m.nature == Message.Nature.CLIENT} == {
        (premiere.id, Etat.SOUMIS),
        (seconde.id, Etat.REJETE),
    }
    assert [m.texte for m in recus if m.nature == Message.Nature.SYSTEME] == [
        CONFIRMATION.replace("{expediteur}", confirmation_expediteur(premiere))
    ]


def confirmation_expediteur(campagne) -> str:
    """L'expéditeur que la personne a lu : celui du message de la campagne."""
    return Message.objects.get(campagne_id=campagne.id, numero_normalise=NUMERO).expediteur_libelle
