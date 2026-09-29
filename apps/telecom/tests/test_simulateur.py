"""Connecteur simulé, T-13."""

from collections import Counter
from datetime import timedelta

import pytest
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.services import creer_client
from apps.journal.models import Etat, Evenement, Message
from apps.journal.services import appliquer_accuse, enregistrer_message, soumettre
from apps.telecom.models import Connecteur, Operateur
from apps.telecom.passerelles import Simulateur, passerelle_pour

# Transactions réelles : le connecteur, créé par la connexion worker, doit être
# validé pour que la clé étrangère de journal_message le trouve.
pytestmark = pytest.mark.django_db(transaction=True, databases=["default", "worker"])


def _connecteur(**parametres):
    return Connecteur.objects.using("worker").create(
        nom=f"simulateur-{len(parametres)}-{timezone.now().timestamp()}",
        operateur=Operateur.ORANGE,
        type=Connecteur.Type.SIMULE,
        parametres=parametres,
    )


@pytest.fixture
def client(en_tant_que_worker):
    # Sous le rôle worker, comme le worker d'envoi : pas de client à poser, et
    # aucune transaction ouverte autour de la soumission.
    return creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)


def _message(client, numero="+22670123456"):
    return enregistrer_message(
        client=client,
        numero_normalise=numero,
        operateur="ORANGE",
        expediteur_libelle="EXEMPLE",
        texte="Votre code est 4821",
        cout_unitaire=10,
    )


def test_une_soumission_traverse_la_couche_d_abstraction_et_son_accuse_change_l_etat(client):
    passerelle = passerelle_pour(_connecteur(taux_echec=0, graine=1))
    assert isinstance(passerelle, Simulateur)

    message = soumettre(_message(client), passerelle)
    assert message.etat == Etat.SOUMIS
    assert message.reference_smsc and message.connecteur_id == passerelle.connecteur.id

    accuses = passerelle.accuses_dus()
    assert [a.libelle_operateur for a in accuses] == ["DELIVRD"]
    assert appliquer_accuse(accuses[0]) is True

    message = Message.objects.get(id=message.id)
    assert message.etat == Etat.LIVRE and message.date_accuse is not None
    historique = Evenement.objects.filter(message_id=message.id).order_by("horodatage", "id")
    assert [e.etat for e in historique] == [Etat.RECU, Etat.SOUMIS, Etat.LIVRE]


def test_un_accuse_rejoue_ne_produit_pas_de_seconde_transition(client):
    passerelle = passerelle_pour(_connecteur(taux_echec=0))
    soumettre(_message(client), passerelle)
    accuse = passerelle.accuses_dus()[0]
    assert appliquer_accuse(accuse) is True
    assert appliquer_accuse(accuse) is False
    assert Evenement.objects.filter(etat=Etat.LIVRE).count() == 1


def test_l_accuse_arrive_apres_le_delai_regle(client):
    instant = [timezone.now()]
    connecteur = _connecteur(delai_accuse_s=60, taux_echec=0)
    passerelle = passerelle_pour(connecteur, horloge=lambda: instant[0])
    soumettre(_message(client), passerelle)

    assert passerelle.accuses_dus(instant[0] + timedelta(seconds=59)) == []
    accuses = passerelle.accuses_dus(instant[0] + timedelta(seconds=60))
    assert len(accuses) == 1
    assert accuses[0].horodatage - instant[0] == timedelta(seconds=60)


def test_sur_mille_messages_a_cinq_pour_cent_les_etats_et_le_delai_sont_conformes(client):
    """T-21 : la répartition suit le taux réglé, et l'écart entre soumission et
    accusé suit le délai réglé."""
    passerelle = passerelle_pour(_connecteur(taux_echec=0.05, delai_accuse_s=30, graine=2026))
    for rang in range(1_000):
        soumettre(_message(client, numero=f"+2267{rang:07d}"), passerelle)
    for accuse in passerelle.accuses_dus(timezone.now() + timedelta(seconds=31)):
        appliquer_accuse(accuse)

    etats = Counter(Message.objects.values_list("etat", flat=True))
    assert sum(etats.values()) == 1_000
    assert set(etats) == {Etat.LIVRE, Etat.NON_LIVRE}
    # Loi binomiale : 50 attendus, écart type de 6,9 ; marge de quatre écarts types.
    assert 22 <= etats[Etat.NON_LIVRE] <= 78
    ecarts = [
        (accuse - soumis).total_seconds()
        for soumis, accuse in Message.objects.values_list("date_soumission", "date_accuse")
    ]
    assert all(29.5 <= ecart <= 31 for ecart in ecarts), (min(ecarts), max(ecarts))


def test_un_libelle_inconnu_d_un_operateur_tombe_dans_un_etat_par_defaut():
    passerelle = Simulateur(Connecteur(type=Connecteur.Type.SIMULE, parametres={}))
    assert passerelle.traduire("delivrd") == Etat.LIVRE
    assert passerelle.traduire("ENROUTE") == Etat.NON_LIVRE


def test_le_role_applicatif_ne_lit_pas_les_connecteurs():
    _connecteur()
    assert Connecteur.objects.count() == 0
    assert Connecteur.objects.using("worker").count() >= 1


def test_un_type_sans_adaptateur_est_refuse():
    with pytest.raises(NotImplementedError):
        passerelle_pour(Connecteur(type=Connecteur.Type.HTTP))
