"""Rôles et permissions, T-35 : chaque route, chaque rôle, le code attendu.

La table ATTENDUS doit couvrir toutes les routes nommées de l'espace client :
une route ajoutée sans y être déclarée fait échouer le test, si bien qu'aucune
page ne peut naître sans que ses droits aient été pensés.
"""

import uuid

import pytest
from django.urls import URLPattern, URLResolver, get_resolver, reverse
from django.utils import timezone

from apps.comptes.models import Client
from apps.comptes.permissions import PERMISSIONS, autorise
from apps.comptes.services import creer_client, creer_utilisateur

pytestmark = pytest.mark.django_db

ANONYME, UTILISATEUR, ADMIN = "anonyme", "UTILISATEUR", "ADMIN"
CONNEXION = 302  # renvoi vers la page de connexion
PUBLIC = {ANONYME: 200, UTILISATEUR: 200, ADMIN: 200}

# route : (méthode, codes attendus par rôle)
ATTENDUS = {
    "connexion": ("get", PUBLIC),
    "deconnexion": ("post", {ANONYME: 302, UTILISATEUR: 302, ADMIN: 302}),
    "invitation": ("get", {ANONYME: 404, UTILISATEUR: 404, ADMIN: 404}),
    "verification": ("get", {ANONYME: 404, UTILISATEUR: 404, ADMIN: 404}),
    "mot-de-passe-oubli": ("get", PUBLIC),
    "mot-de-passe-reinitialiser": ("get", {ANONYME: 404, UTILISATEUR: 404, ADMIN: 404}),
    "mot-de-passe-changer": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "renvoi": ("post", PUBLIC),
    "accueil": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "membres": ("get", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 200}),
    "membre-role": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 302}),
    "membre-retirer": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 302}),
    # Sans nom, la création est refusée et la page se réaffiche.
    "cles-api": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 200}),
    "cle-api-revoquer": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 404}),
    "webhooks": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 200}),
    "webhook-livraisons": ("get", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 200}),
    "webhook-desactiver": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 404}),
    "webhook-reactiver": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 404}),
    "alerte-solde": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 302}),
    "second-facteur-activer": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "second-facteur-codes": ("get", {ANONYME: CONNEXION, UTILISATEUR: 302, ADMIN: 302}),
    "second-facteur-verifier": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "metriques": ("get", {ANONYME: 401, UTILISATEUR: 401, ADMIN: 401}),
    "rappel-jasmin": ("post", {ANONYME: 403, UTILISATEUR: 403, ADMIN: 403}),
    # API publique : seule la clé authentifie, une session ne vaut rien.
    "api-messages": ("post", {ANONYME: 401, UTILISATEUR: 401, ADMIN: 401}),
    "api-message": ("get", {ANONYME: 401, UTILISATEUR: 401, ADMIN: 401}),
    "api-solde": ("get", {ANONYME: 401, UTILISATEUR: 401, ADMIN: 401}),
    "api-expediteurs": ("get", {ANONYME: 401, UTILISATEUR: 401, ADMIN: 401}),
    # Documentation publique de l'API (T-110).
    "api-openapi": ("get", {ANONYME: 200, UTILISATEUR: 200, ADMIN: 200}),
    "api-documentation": ("get", {ANONYME: 200, UTILISATEUR: 200, ADMIN: 200}),
    # Point public des fournisseurs : sans signature, la même réponse nue pour tous.
    "rappel-paiement": ("post", {ANONYME: 403, UTILISATEUR: 403, ADMIN: 403}),
    # Sans montant, le rechargement est refusé et le formulaire se réaffiche.
    "recharger": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 200}),
    "paiement": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "factures": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "donnees": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    # Sans période, l'export du journal est refusé.
    "journal": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "journal-export": ("get", {ANONYME: CONNEXION, UTILISATEUR: 400, ADMIN: 400}),
    "journal-message": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "facture": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "paiement-statut": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    # La page du fournisseur simulé : un paiement inconnu n'existe pas.
    "simulateur-paiement": ("get", {ANONYME: 404, UTILISATEUR: 404, ADMIN: 404}),
    "simulateur-choix": ("post", {ANONYME: 404, UTILISATEUR: 404, ADMIN: 404}),
    "campagnes": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "campagne-nouvelle": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "campagne": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "campagne-test": ("post", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "campagne-confirmer": ("post", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "campagne-pause": ("post", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "campagne-reprendre": ("post", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "campagne-annuler": ("post", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "campagne-suivi": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "tableau-de-bord": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "support": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "support-nouveau": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "support-ticket": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "support-ticket-fil": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "tableau-de-bord-indicateurs": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "campagnes-modeles": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "campagnes-modele": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "contacts-repertoire": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "contacts-resultats": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "contacts-exporter": ("post", {ANONYME: CONNEXION, UTILISATEUR: 302, ADMIN: 302}),
    "contacts-export-telecharger": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "contacts-listes": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "contacts-liste-supprimer": ("post", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "contacts-liste-noire": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    "contacts-champs": ("get", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 200}),
    "contacts-champ-supprimer": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 404}),
    "expediteurs": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    # Sans pièce jointe, la déclaration est refusée et renvoie vers la liste.
    "expediteurs-declarer": ("post", {ANONYME: CONNEXION, UTILISATEUR: 403, ADMIN: 302}),
    "import-deposer": ("get", {ANONYME: CONNEXION, UTILISATEUR: 200, ADMIN: 200}),
    # Un import inexistant, ou d'un autre compte, n'existe pas pour le visiteur.
    "import-mappage": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "import-suivi": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "import-progression": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
    "import-rejets": ("get", {ANONYME: CONNEXION, UTILISATEUR: 404, ADMIN: 404}),
}


def _routes_nommees(motifs=None, prefixe=""):
    for motif in motifs if motifs is not None else get_resolver().url_patterns:
        if isinstance(motif, URLResolver):
            if str(motif.pattern).startswith("admin/"):
                continue
            yield from _routes_nommees(motif.url_patterns, prefixe)
        elif isinstance(motif, URLPattern) and motif.name:
            yield motif.name


@pytest.fixture
def membres():
    client = creer_client(raison_sociale="Exemple SA", forme=Client.Forme.SOCIETE)
    commun = {
        "client": client,
        "courriel_verifie_le": timezone.now(),
        "mot_de_passe": "x-1234-abcd",
    }
    admin = creer_utilisateur(courriel="a@exemple.bf", nom="A", role=ADMIN, **commun)
    creer_utilisateur(courriel="a2@exemple.bf", nom="A2", role=ADMIN, **commun)
    simple = creer_utilisateur(courriel="u@exemple.bf", nom="U", role=UTILISATEUR, **commun)
    cible = creer_utilisateur(courriel="c@exemple.bf", nom="C", role=UTILISATEUR, **commun)
    return {ADMIN: admin, UTILISATEUR: simple, "cible": cible}


def _adresse(nom, membres):
    if nom in ("membre-role", "membre-retirer"):
        return reverse(nom, args=[membres["cible"].pk])
    if nom in ("invitation", "verification", "mot-de-passe-reinitialiser"):
        return reverse(nom, args=["jeton-invalide"])
    if nom in (
        "rappel-jasmin",
        "rappel-paiement",
        "paiement",
        "paiement-statut",
        "facture",
        "simulateur-paiement",
        "simulateur-choix",
        "campagnes-modele",
        "campagne",
        "campagne-test",
        "campagne-confirmer",
        "campagne-pause",
        "campagne-reprendre",
        "campagne-annuler",
        "campagne-suivi",
        "journal-message",
        "support-ticket",
        "support-ticket-fil",
        "contacts-liste-supprimer",
        "contacts-champ-supprimer",
        "contacts-export-telecharger",
        "cle-api-revoquer",
        "api-message",
        "webhook-desactiver",
        "webhook-reactiver",
    ) or (nom.startswith("import-") and nom != "import-deposer"):
        return reverse(nom, args=[uuid.uuid7()])
    return reverse(nom)


def test_chaque_route_de_l_espace_client_a_ses_droits_declares():
    assert set(_routes_nommees()) == set(ATTENDUS)


@pytest.mark.parametrize("role", [ANONYME, UTILISATEUR, ADMIN])
@pytest.mark.parametrize("nom", sorted(ATTENDUS))
def test_chaque_route_refuse_ou_sert_selon_le_role(client, membres, role, nom):
    methode, codes = ATTENDUS[nom]
    if role != ANONYME:
        client.force_login(membres[role])
    donnees = {"role": UTILISATEUR, "courriel": "x@exemple.bf"} if methode == "post" else None
    reponse = getattr(client, methode)(_adresse(nom, membres), donnees)
    assert reponse.status_code == codes[role], (nom, role, reponse.status_code)
    if role == ANONYME and codes[role] == CONNEXION and nom not in ("deconnexion",):
        assert reponse["Location"].startswith("/connexion?next=")


def test_un_utilisateur_sans_compte_client_n_a_aucune_permission(membres):
    agent = creer_utilisateur(courriel="agent@el-immeka.test", mot_de_passe="x", nom="Agent")
    assert not any(autorise(agent, permission) for permission in PERMISSIONS)


def test_une_permission_inconnue_est_une_erreur(membres):
    with pytest.raises(KeyError):
        autorise(membres[ADMIN], "membres.effacer")
