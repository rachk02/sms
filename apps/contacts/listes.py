"""Listes de contacts et leur compteur de membres.

Le compteur est tenu dans la transaction même de l'ajout ou du retrait, par le
nombre de lignes réellement insérées ou supprimées : deux opérations
concurrentes sur un même membre ne le faussent pas, la contrainte d'unicité et
le verrou de ligne tranchant pour elles. Retirer un contact d'une liste ne le
retire pas du répertoire ; supprimer une liste ne supprime aucun contact.
"""

from django.db import connection, transaction
from django.db.models import F

from .models import Liste, ListeContact

AJOUT = """
INSERT INTO contacts_liste_contact (liste_id, contact_id, client_id, ajoute_le)
SELECT %s, id, client_id, now() FROM contacts_contact
WHERE id = ANY(%s::uuid[]) AND client_id = %s
ON CONFLICT DO NOTHING
"""

AJOUT_DEPUIS_IMPORT = """
INSERT INTO contacts_liste_contact (liste_id, contact_id, client_id, ajoute_le)
SELECT %s, c.id, c.client_id, now()
FROM contacts_contact c
JOIN (SELECT DISTINCT numero_normalise FROM contacts_import_transit WHERE import_id = %s) t
  ON t.numero_normalise = c.numero_normalise
WHERE c.client_id = %s
ON CONFLICT DO NOTHING
"""


class ListeRefusee(ValueError):
    pass


def creer(*, client, nom: str, description: str = "") -> Liste:
    nom = nom.strip()
    if not nom:
        raise ListeRefusee("Une liste porte un nom.")
    if Liste.objects.filter(client=client, nom=nom).exists():
        raise ListeRefusee(f"Une liste « {nom} » existe déjà.")
    return Liste.objects.create(client=client, nom=nom, description=description.strip())


def _compter(liste_id, variation: int) -> None:
    if variation:
        Liste.objects.filter(id=liste_id).update(nombre_contacts=F("nombre_contacts") + variation)


@transaction.atomic
def ajouter(liste: Liste, contact_ids) -> int:
    """Ajoute des contacts du même client ; renvoie le nombre réellement ajoutés."""
    with connection.cursor() as curseur:
        curseur.execute(AJOUT, [liste.id, [str(i) for i in contact_ids], liste.client_id])
        ajoutes = curseur.rowcount
    _compter(liste.id, ajoutes)
    return ajoutes


def ajouter_depuis_import(liste_id, import_id, client_id) -> int:
    """Ajout en masse des contacts d'un import, déjà connus ou nouveaux. À
    appeler dans la transaction de fusion, avant que la table de transit soit
    vidée."""
    with connection.cursor() as curseur:
        curseur.execute(AJOUT_DEPUIS_IMPORT, [liste_id, import_id, client_id])
        ajoutes = curseur.rowcount
    _compter(liste_id, ajoutes)
    return ajoutes


@transaction.atomic
def retirer(liste: Liste, contact_ids) -> int:
    retires, _ = ListeContact.objects.filter(liste=liste, contact_id__in=contact_ids).delete()
    _compter(liste.id, -retires)
    return retires


@transaction.atomic
def supprimer(liste: Liste) -> int:
    """Supprime la liste et ses appartenances, jamais les contacts. Renvoie le
    nombre de contacts qui en faisaient partie."""
    membres = ListeContact.objects.filter(liste=liste).count()
    ListeContact.objects.filter(liste=liste).delete()
    liste.delete()
    return membres


def recompter(liste: Liste) -> int:
    """Le compte réel, pour contrôler le compteur."""
    return ListeContact.objects.filter(liste=liste).count()
