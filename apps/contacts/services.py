"""Porte d'écriture du module contacts."""

from datetime import timedelta

from django.db import IntegrityError, connection, transaction
from django.utils import timezone

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


# Effacement des contacts d'un compte clos (T-89)

# Décision du 6 novembre 2026 : trente jours après la clôture.
DELAI_EFFACEMENT = timedelta(days=30)

# Dans l'ordre des clés étrangères. La liste noire reste : elle prouve qu'un
# abonné s'est opposé aux envois, et les consentements vivent dans conformite.
TABLES_EFFACEES = (
    "contacts_liste_contact",
    "contacts_import_rejet",
    "contacts_import_transit",
    "contacts_contact",
    "contacts_liste",
    "contacts_segment",
    "contacts_champ",
    "contacts_import",
    "contacts_export",
)


def effacer_comptes_clos(maintenant=None) -> int:
    """Balayage de l'ordonnanceur : les contacts des comptes clos depuis plus de
    trente jours, leurs listes, segments, champs, imports et exports, et les
    fichiers d'import et d'export du stockage, sont effacés pour de bon. Rien
    n'est gardé en corbeille. Les factures, les justificatifs, le registre de
    crédits et le journal d'audit ne sont pas touchés. Sous le rôle worker.
    Rend le nombre de contacts effacés."""
    from apps.comptes.models import Client
    from apps.core.audit import auditer
    from apps.fichiers.models import Fichier
    from apps.fichiers.services import supprimer

    from .models import Export, Import

    maintenant = maintenant or timezone.now()
    clos = Client.objects.filter(
        etat=Client.Etat.CLOTURE, cloture_le__lte=maintenant - DELAI_EFFACEMENT
    )
    effaces = 0
    for client in clos:
        if not Contact.objects.filter(client=client).exists() and not (
            Import.objects.filter(client=client).exists()
            or Export.objects.filter(client=client).exists()
        ):
            continue
        fichiers = [
            *Import.objects.filter(client=client).values_list("fichier_id", flat=True),
            *Export.objects.filter(client=client).values_list("fichier_id", flat=True),
        ]
        # Le stockage d'abord, hors de toute transaction : une panne laisse les
        # lignes, et le balayage suivant reprend.
        for fichier in Fichier.objects.filter(id__in=[f for f in fichiers if f]):
            supprimer(fichier)
        with transaction.atomic(), connection.cursor() as curseur:
            nombre = Contact.objects.filter(client=client).count()
            for table in TABLES_EFFACEES:
                curseur.execute(f"DELETE FROM {table} WHERE client_id = %s", [client.id])
            auditer(
                "contacts.effacement",
                client,
                client=client,
                apres={"contacts": nombre, "fichiers": len([f for f in fichiers if f])},
                motif=f"Compte clos le {timezone.localtime(client.cloture_le):%d/%m/%Y}",
            )
        effaces += nombre
    return effaces
