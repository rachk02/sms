"""Porte d'écriture du module comptes."""

from django.db import transaction

from apps.core.cloisonnement import client_pose, poser_client

from .models import Client, Utilisateur

# Plafonds d'envoi par palier, en messages. Valeurs provisoires : la fiche F-11
# laisse les plafonds de chaque palier à trancher. Elles ne servent qu'à remplir
# les colonnes non nulles tant que la décision n'est pas prise.
PLAFONDS_PAR_PALIER = {
    Client.Palier.DECOUVERTE: {"horaire": 100, "journalier": 500},
    Client.Palier.VERIFIE: {"horaire": 1_000, "journalier": 5_000},
    Client.Palier.CONTRACTUEL: {"horaire": 50_000, "journalier": 500_000},
}


@transaction.atomic
def creer_client(*, raison_sociale, forme, palier=Client.Palier.DECOUVERTE, **champs) -> Client:
    """Crée un client. La politique de cloisonnement n'accepte une ligne de
    `comptes_client` que pour le client posé : le client créé est donc posé le
    temps de l'insertion, puis le client précédent est rétabli."""
    plafonds = PLAFONDS_PAR_PALIER[palier]
    champs.setdefault("plafond_horaire", plafonds["horaire"])
    champs.setdefault("plafond_journalier", plafonds["journalier"])
    client = Client(raison_sociale=raison_sociale, forme=forme, palier=palier, **champs)
    # L'unicité du RCCM et de l'IFU est vérifiée par la base : sous cloisonnement,
    # une vérification applicative ne verrait pas les autres clients.
    client.full_clean(validate_unique=False, validate_constraints=False)
    precedent = client_pose()
    poser_client(client.id)
    client.save(force_insert=True)
    poser_client(precedent or None)
    return client


@transaction.atomic
def creer_utilisateur(*, courriel, mot_de_passe, nom, client=None, **champs) -> Utilisateur:
    return Utilisateur.objects.create_user(courriel, mot_de_passe, nom=nom, client=client, **champs)
