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


class Cloisonne(models.Model):
    """Colonne `client_id` sur laquelle porte la politique de cloisonnement par
    ligne. Référence par chaîne : `core` n'importe pas `comptes`."""

    client = models.ForeignKey("comptes.Client", on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True


class BaseClient(Base, Cloisonne):
    class Meta:
        abstract = True
