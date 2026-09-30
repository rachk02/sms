"""Porte d'écriture du module contacts."""

from django.db import IntegrityError, transaction

from apps.core.numeros import normaliser
from apps.telecom.prefixes import classer

from .models import Contact


class ContactExistant(ValueError):
    def __init__(self, numero_normalise: str):
        self.numero_normalise = numero_normalise
        super().__init__(f"{numero_normalise} figure déjà dans le répertoire.")


def creer_contact(
    *,
    client,
    numero: str,
    origine: str,
    nom: str = "",
    prenom: str = "",
    ville: str = "",
    champs: dict | None = None,
    prefixes: dict | None = None,
) -> Contact:
    """Normalise le numéro, présume l'opérateur, écrit le contact.

    L'unicité par client est garantie par la base, non par une vérification
    préalable : sur deux écritures simultanées, seule la contrainte tranche
    sans erreur possible. Lève NumeroInvalide ou ContactExistant.
    """
    numero_normalise = normaliser(numero)
    try:
        with transaction.atomic():
            return Contact.objects.create(
                client=client,
                numero_normalise=numero_normalise,
                numero_origine=numero[:50],
                operateur_presume=classer(numero_normalise, client.pays, prefixes),
                nom=nom.strip(),
                prenom=prenom.strip(),
                ville=ville.strip(),
                champs=champs or {},
                origine=origine,
            )
    except IntegrityError:
        raise ContactExistant(numero_normalise) from None


def confirmer_operateur(client_id, numero_normalise: str, operateur: str) -> int:
    """Un accusé livré par la route d'un opérateur confirme que le numéro est
    chez lui : l'opérateur n'est plus présumé. Renvoie le nombre de contacts
    mis à jour."""
    return (
        Contact.objects.filter(client_id=client_id, numero_normalise=numero_normalise)
        .exclude(operateur_confirme=operateur)
        .update(operateur_confirme=operateur)
    )
