"""Envoi unitaire de bout en bout, T-24."""

import socket
import subprocess
import time

import pytest
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.core.etats import Etat
from apps.journal.models import Message
from apps.journal.services import envoyer_unitaire
from apps.telecom import correlation
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import passerelle_pour

pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])

TEXTE_400 = "".join(chr(ord("a") + rang % 26) for rang in range(400))


@pytest.fixture
def client(en_tant_que_worker):
    return creer_client(
        raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE, palier=Client.Palier.VERIFIE
    )


def _connecteur(type_=Connecteur.Type.SIMULE, **parametres):
    return Connecteur.objects.create(
        nom=f"c-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=type_,
        parametres=parametres,
    )


def _envoyer(client, passerelle, texte, numero="70 12 34 56"):
    message = envoyer_unitaire(
        client=client,
        numero=numero,
        texte=texte,
        expediteur=settings.SMS_NUMERO_COURT,
        passerelle=passerelle,
        cout_unitaire=10,
    )
    correlation._redis().delete(
        correlation.cle_redis(passerelle.connecteur.id, message.reference_smsc)
    )
    return message


def test_un_texte_de_400_caracteres_part_en_trois_segments_enchaines(client):
    passerelle = passerelle_pour(_connecteur())
    message = _envoyer(client, passerelle, TEXTE_400)

    assert (message.numero_normalise, message.etat) == ("+22670123456", Etat.SOUMIS)
    assert (message.encodage, message.segments, message.cout_total) == ("GSM7", 3, 30)
    recus = passerelle.segments[message.reference_smsc]
    assert [(s.rang, s.total) for s in recus] == [(1, 3), (2, 3), (3, 3)]
    assert len({s.reference for s in recus}) == 1
    assert "".join(s.contenu for s in recus) == TEXTE_400


def test_un_caractere_hors_table_gsm_bascule_en_ucs2_et_le_nombre_de_segments_suit(client):
    passerelle = passerelle_pour(_connecteur())
    texte = "Fête " * 20  # 100 caractères avec ê
    message = _envoyer(client, passerelle, texte)
    assert (message.encodage, message.segments) == ("UCS2", 2)
    assert len(passerelle.segments[message.reference_smsc]) == 2


def test_le_message_est_valide_au_journal_avant_la_soumission(client):
    passerelle = passerelle_pour(_connecteur())
    vu_avant = []
    soumettre_reel = passerelle.soumettre

    def soumettre_en_verifiant(soumission):
        # Lecture par une autre connexion : ne voit que ce qui est validé.
        vu_avant.append(
            Message.objects.using("worker")
            .filter(numero_normalise=soumission.numero, etat=Etat.RECU)
            .exists()
        )
        return soumettre_reel(soumission)

    passerelle.soumettre = soumettre_en_verifiant
    _envoyer(client, passerelle, "Bonjour", numero="+22670999999")
    assert vu_avant == [True]


def test_l_envoi_unitaire_refuse_une_transaction_deja_ouverte(client):
    passerelle = passerelle_pour(_connecteur())
    with pytest.raises(RuntimeError, match="hors transaction"), transaction.atomic():
        envoyer_unitaire(
            client=client,
            numero="70123456",
            texte="x",
            expediteur="E",
            passerelle=passerelle,
            cout_unitaire=10,
        )


def _jasmin_lance() -> bool:
    try:
        port = int(settings.JASMIN_HTTP_URL.rsplit(":", 1)[1])
        socket.create_connection(("127.0.0.1", port), timeout=1).close()
        return bool(settings.JASMIN_HTTP_USER)
    except OSError, ValueError, IndexError:
        return False


@pytest.mark.skipif(not _jasmin_lance(), reason="Jasmin n'est pas lancé (profil passerelle)")
def test_par_jasmin_le_simulateur_smpp_recoit_trois_segments_concatenes(client, settings):
    settings.JASMIN_URL_RAPPEL = ""
    numero = f"+2267{int(time.time()) % 10_000_000:07d}"
    depuis = time.time() - 1
    message = _envoyer(
        client, passerelle_pour(_connecteur(Connecteur.Type.SMPP)), TEXTE_400, numero
    )
    assert message.segments == 3

    fin, lignes = time.monotonic() + 15, []
    while time.monotonic() < fin:
        journal = subprocess.run(
            ["docker", "compose", "logs", "smsc", "--since", str(int(depuis))],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        ).stdout
        cible = f"dest_addr={numero[1:]},"
        lignes = [ligne for ligne in journal.splitlines() if cible in ligne]
        if len(lignes) >= 3:
            break
        time.sleep(0.5)
    assert len(lignes) == 3, lignes
    assert all("esm_class=64" in ligne for ligne in lignes)  # indicateur d'en-tête UDH
