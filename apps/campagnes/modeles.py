"""Modèles de message : enregistrement après analyse des balises (T-55)."""

from apps.contacts.models import ChampPersonnalise

from .models import Modele
from .personnalisation import Gabarit, ModeleInvalide, compiler


def replis_declares(client) -> dict[str, str]:
    return dict(ChampPersonnalise.objects.filter(client=client).values_list("nom", "valeur_repli"))


def gabarit(client, texte: str) -> Gabarit:
    """Le texte analysé avec les champs que le client a déclarés."""
    return compiler(texte, replis_declares(client))


def enregistrer(*, client, nom: str, texte: str, modele: Modele | None = None) -> Modele:
    """Crée ou modifie un modèle. Une balise sans valeur de repli, inconnue ou
    mal fermée est refusée ici, pas à l'envoi."""
    nom = nom.strip()
    if not nom:
        raise ModeleInvalide("Un modèle porte un nom.")
    variables = [balise.nom for balise in gabarit(client, texte).balises]
    homonymes = Modele.objects.filter(client=client, nom=nom)
    if modele is not None:
        homonymes = homonymes.exclude(id=modele.id)
    if homonymes.exists():
        raise ModeleInvalide(f"Un modèle « {nom} » existe déjà.")
    if modele is None:
        return Modele.objects.create(client=client, nom=nom, texte=texte, variables=variables)
    modele.nom, modele.texte, modele.variables = nom, texte, variables
    modele.save(update_fields=["nom", "texte", "variables", "modifie_le"])
    return modele
