"""Porte d'écriture du module conformite."""

from django.db import models

from .models import Audit


def _cle(objet) -> tuple[str, str]:
    if isinstance(objet, models.Model):
        return objet._meta.label_lower, str(objet.pk)
    type_, identifiant = objet
    return type_, str(identifiant)


def auditer(
    action: str,
    objet,
    *,
    auteur=None,
    client=None,
    avant: dict | None = None,
    apres: dict | None = None,
    motif: str = "",
    adresse_ip: str | None = None,
) -> Audit:
    """Écrit une action au journal d'audit. `objet` est une instance de modèle
    ou un couple (type, identifiant). À appeler dans la transaction de l'action
    elle-même : l'écriture et sa trace sont validées ensemble, ou pas du tout."""
    objet_type, objet_id = _cle(objet)
    return Audit.objects.create(
        action=action,
        objet_type=objet_type,
        objet_id=objet_id,
        auteur=auteur,
        client=client,
        valeur_avant=avant,
        valeur_apres=apres,
        motif=motif,
        adresse_ip=adresse_ip,
    )
