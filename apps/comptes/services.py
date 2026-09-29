"""Porte d'écriture du module comptes."""

import secrets
from datetime import timedelta

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction
from django.utils import timezone

from apps.core.cloisonnement import client_pose, poser_client

from .models import Client, NumeroTest, Utilisateur

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


class NumeroTestRefuse(ValueError):
    pass


CODE_VALIDITE = timedelta(minutes=10)


@transaction.atomic
def declarer_numero_test(client, numero_normalise: str) -> tuple[NumeroTest, str]:
    """Déclare un numéro de test et renvoie le code à six chiffres à lui envoyer.
    Seul le condensat du code est conservé."""
    if client.numeros_test.count() >= NumeroTest.MAXIMUM_PAR_CLIENT:
        raise NumeroTestRefuse(f"{NumeroTest.MAXIMUM_PAR_CLIENT} numéros de test au plus.")
    code = f"{secrets.randbelow(1_000_000):06d}"
    numero, _ = NumeroTest.objects.update_or_create(
        client=client,
        numero_normalise=numero_normalise,
        defaults={
            "code_condensat": make_password(code),
            "code_expire_le": timezone.now() + CODE_VALIDITE,
            "verifie_le": None,
        },
    )
    return numero, code


@transaction.atomic
def verifier_numero_test(client, numero_normalise: str, code: str) -> bool:
    numero = (
        NumeroTest.objects.select_for_update()
        .filter(client=client, numero_normalise=numero_normalise)
        .first()
    )
    if (
        numero is None
        or not numero.code_condensat
        or numero.code_expire_le is None
        or numero.code_expire_le < timezone.now()
        or not check_password(code, numero.code_condensat)
    ):
        return False
    numero.verifie_le, numero.code_condensat = timezone.now(), ""
    numero.save(update_fields=["verifie_le", "code_condensat", "modifie_le"])
    return True


def envoi_autorise(client, numero_normalise: str) -> bool:
    """Le bac à sable : un compte au palier Découverte n'envoie qu'à ses numéros
    de test vérifiés. Contrôlé dans le moteur d'envoi, jamais dans le portail
    seul, qui serait contourné par le premier appel d'API."""
    if client.palier != Client.Palier.DECOUVERTE:
        return True
    return NumeroTest.objects.filter(
        client=client, numero_normalise=numero_normalise, verifie_le__isnull=False
    ).exists()
