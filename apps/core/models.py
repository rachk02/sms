"""Modèles abstraits partagés par tous les modules.

Aucune table ici : seulement les colonnes que chaque table métier porte.
"""

import uuid

from django.db import models


class Identifiable(models.Model):
    """Clé primaire UUID. Version 7 : ordonnée dans le temps, donc des index
    compacts sur les grosses tables, et jamais une clé séquentielle exposée."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid7, editable=False)

    class Meta:
        abstract = True


class Horodate(models.Model):
    cree_le = models.DateTimeField(auto_now_add=True)
    modifie_le = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class Base(Identifiable, Horodate):
    class Meta:
        abstract = True
