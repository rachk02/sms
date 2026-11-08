"""Tables du module api. Voir docs/modele-de-donnees.md, section api."""

from django.db import models

from apps.core.models import Cloisonne, Identifiable


class Idempotence(Identifiable, Cloisonne):
    """La réponse produite pour un en-tête `Idempotency-Key`, gardée vingt-quatre
    heures (T-108).

    La ligne est réservée au début de la requête, dans sa transaction, par une
    insertion qui ignore le conflit : un rejeu concurrent attend sur l'index
    unique que la première requête soit validée, puis lit sa réponse. Une
    requête qui échoue emporte sa réservation avec elle."""

    cle = models.CharField(max_length=255)
    # SHA-256 de la méthode, du chemin et du corps de la requête.
    empreinte = models.CharField(max_length=64)
    statut_http = models.SmallIntegerField(null=True, blank=True)
    reponse = models.JSONField(null=True, blank=True)
    cree_le = models.DateTimeField(auto_now_add=True)
    expire_le = models.DateTimeField()

    class Meta:
        db_table = "api_idempotence"
        constraints = [
            models.UniqueConstraint(fields=["client", "cle"], name="idempotence_unique_par_client")
        ]
        indexes = [models.Index(fields=["expire_le"], name="idempotence_expiration")]

    def __str__(self):
        return self.cle
