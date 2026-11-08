"""Lectures du module expediteurs."""

from .models import OPERATEURS, Expediteur


def du_compte(client_id) -> list[dict]:
    """Les identifiants d'expéditeur du compte, chacun avec son état sur chaque
    réseau (absent : jamais soumis sur ce réseau)."""
    resultat = []
    for expediteur in (
        Expediteur.objects.filter(client_id=client_id)
        .prefetch_related("reseaux")
        .order_by("libelle")
    ):
        par_operateur = {reseau.operateur: reseau for reseau in expediteur.reseaux.all()}
        resultat.append(
            {"expediteur": expediteur, "reseaux": [par_operateur.get(o) for o in OPERATEURS]}
        )
    return resultat
