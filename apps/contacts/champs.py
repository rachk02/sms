"""Champs personnalisés : déclaration et suppression (T-52)."""

from django.db import transaction
from django.utils.text import slugify

from .models import ChampPersonnalise, Segment

# Les données fixes du contact et les critères de segment : un champ
# personnalisé ne peut pas porter leur nom.
RESERVES = frozenset({"numero", "nom", "prenom", "ville", "operateur", "date_entree", "liste"})


class ChampRefuse(ValueError):
    pass


def nom_de(texte: str) -> str:
    """Le nom technique d'un libellé ou d'un entête de colonne : le même calcul
    des deux côtés, pour que l'entête « Référence client » retrouve le champ
    du même libellé."""
    return slugify(texte).replace("-", "_")[:50]


def declarer(*, client, libelle: str, valeur_repli: str, type_: str = "TEXTE"):
    """Déclare un champ. La valeur de repli est refusée vide : c'est elle qui
    évite un message adressé à personne."""
    libelle, valeur_repli = libelle.strip(), valeur_repli.strip()
    if not libelle:
        raise ChampRefuse("Un champ porte un libellé.")
    if not valeur_repli:
        raise ChampRefuse(
            "La valeur de repli est obligatoire : elle remplace un champ vide dans un message."
        )
    if type_ not in ChampPersonnalise.Type.values:
        raise ChampRefuse("Type de champ inconnu.")
    nom = nom_de(libelle)
    if not nom:
        raise ChampRefuse("Le libellé doit contenir au moins une lettre ou un chiffre.")
    if nom in RESERVES:
        raise ChampRefuse(f"« {libelle} » désigne déjà une donnée du contact.")
    if (
        ChampPersonnalise.objects.filter(client=client).count()
        >= ChampPersonnalise.MAXIMUM_PAR_CLIENT
    ):
        raise ChampRefuse(f"{ChampPersonnalise.MAXIMUM_PAR_CLIENT} champs personnalisés au plus.")
    if ChampPersonnalise.objects.filter(client=client, nom=nom).exists():
        raise ChampRefuse(f"Un champ « {libelle} » existe déjà.")
    return ChampPersonnalise.objects.create(
        client=client, nom=nom, libelle=libelle, valeur_repli=valeur_repli, type=type_
    )


def noms_declares(client) -> set[str]:
    return set(ChampPersonnalise.objects.filter(client=client).values_list("nom", flat=True))


@transaction.atomic
def supprimer(champ: ChampPersonnalise) -> list[Segment]:
    """Supprime le champ. Les valeurs déjà saisies restent sur les contacts ;
    les segments qui s'en servaient sont désactivés, avec un message, plutôt
    que cassés. Renvoie ces segments."""
    touches = [
        segment
        for segment in Segment.objects.filter(client_id=champ.client_id, actif=True)
        if any(critere.get("champ") == champ.nom for critere in segment.criteres)
    ]
    motif = f"Le champ « {champ.libelle} » a été supprimé : revoyez les critères du segment."
    Segment.objects.filter(id__in=[s.id for s in touches]).update(
        actif=False, motif_desactivation=motif
    )
    champ.delete()
    return touches
