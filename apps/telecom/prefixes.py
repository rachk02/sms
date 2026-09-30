"""Classification des numéros par opérateur, d'après la table des préfixes.

Le plus long préfixe qui correspond au numéro national l'emporte. La table est
relue à chaque classification par lot : ajouter une tranche en base change le
comportement sans redémarrage. Le résultat est un opérateur présumé.
"""

import csv
import io

from django.db import transaction

from apps.core.numeros import PLANS

from .models import Operateur, Prefixe


def table(pays: str = "BF") -> dict[str, str]:
    return dict(Prefixe.objects.filter(pays=pays, actif=True).values_list("prefixe", "operateur"))


def classer(numero_normalise: str, pays: str = "BF", prefixes: dict | None = None) -> str:
    """Opérateur présumé d'un numéro normalisé, ou chaîne vide si aucun préfixe
    ne correspond. Passer `prefixes` pour classer un lot sans relire la table."""
    prefixes = table(pays) if prefixes is None else prefixes
    national = numero_normalise.removeprefix("+" + PLANS[pays].indicatif)
    for longueur in range(len(national), 0, -1):
        operateur = prefixes.get(national[:longueur])
        if operateur:
            return operateur
    return ""


class FichierPrefixesInvalide(ValueError):
    pass


def charger(contenu: str, pays: str = "BF", using: str = "backoffice") -> int:
    """Remplace la table d'un pays par le contenu d'un fichier CSV à deux
    colonnes, préfixe et opérateur. Le fichier fait foi : il est tenu par la
    personne chargée du plan de numérotation auprès de l'ARCEP. Passe par un rôle
    qui peut écrire la table : le rôle applicatif ne le peut pas."""
    lignes = []
    for rang, ligne in enumerate(csv.reader(io.StringIO(contenu)), start=1):
        if not ligne or ligne[0].strip().startswith("#") or ligne[0].strip() == "prefixe":
            continue
        prefixe, operateur = (valeur.strip() for valeur in ligne[:2])
        if not prefixe.isdigit() or operateur not in Operateur.values:
            raise FichierPrefixesInvalide(f"Ligne {rang} invalide : {ligne}")
        lignes.append(Prefixe(pays=pays, prefixe=prefixe, operateur=operateur))
    with transaction.atomic(using=using):
        Prefixe.objects.using(using).filter(pays=pays).delete()
        Prefixe.objects.using(using).bulk_create(lignes)
    return len(lignes)
