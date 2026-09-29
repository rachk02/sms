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


class EvenementSortant(Identifiable):
    """Boîte d'envoi des événements internes.

    `publier` y écrit dans la transaction de l'écriture métier : l'événement
    existe si et seulement si l'écriture a été validée. Le relais le publie
    ensuite sur RabbitMQ. Le rôle applicatif peut y écrire mais pas y lire.
    """

    nom = models.CharField(max_length=100)
    client_id = models.UUIDField(null=True, blank=True)
    objet_id = models.UUIDField(null=True, blank=True)
    auteur_id = models.UUIDField(null=True, blank=True)
    donnees = models.JSONField(default=dict, blank=True)
    cle_idempotence = models.CharField(max_length=100, unique=True)
    # Échange de destination : `evenements` pour le bus, `sms` pour déposer un
    # message dans une file de trafic (le nom sert alors de clé de routage).
    echange = models.CharField(max_length=100, default="evenements")
    cree_le = models.DateTimeField(auto_now_add=True)
    publie_le = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "core_evenement_sortant"
        indexes = [
            models.Index(
                fields=["cree_le"],
                condition=models.Q(publie_le__isnull=True),
                name="evenement_a_publier",
            )
        ]

    def __str__(self):
        return self.nom


class EvenementTraite(models.Model):
    """Trace des événements déjà traités, par consommateur. Écrite dans la même
    transaction que le traitement : un événement relivré après un traitement
    validé est reconnu et ignoré."""

    pk = models.CompositePrimaryKey("consommateur", "cle_idempotence")
    consommateur = models.CharField(max_length=100)
    cle_idempotence = models.CharField(max_length=100)
    traite_le = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "core_evenement_traite"

    def __str__(self):
        return f"{self.consommateur} {self.cle_idempotence}"
