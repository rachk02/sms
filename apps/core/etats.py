"""Les dix états du cycle de vie d'un message, dont six terminaux.

Définis dans `core` pour que `telecom`, qui traduit les statuts des opérateurs,
puisse les nommer sans importer `journal`, qui est au-dessus de lui.
"""

from django.db import models


class Etat(models.TextChoices):
    RECU = "RECU", "Reçu"
    EN_ATTENTE = "EN_ATTENTE", "En attente"
    EN_FILE = "EN_FILE", "En file"
    SOUMIS = "SOUMIS", "Soumis"
    LIVRE = "LIVRE", "Livré"
    NON_LIVRE = "NON_LIVRE", "Non livré"
    EXPIRE = "EXPIRE", "Expiré"
    SANS_ACCUSE = "SANS_ACCUSE", "Sans accusé"
    REJETE = "REJETE", "Rejeté"
    ANNULE = "ANNULE", "Annulé"


ETATS_TERMINAUX = frozenset(
    {Etat.LIVRE, Etat.NON_LIVRE, Etat.EXPIRE, Etat.SANS_ACCUSE, Etat.REJETE, Etat.ANNULE}
)

# États qu'un accusé d'opérateur peut annoncer. SANS_ACCUSE naît de l'absence
# d'accusé, jamais d'un accusé ; ANNULE, d'une décision du client.
ETATS_D_ACCUSE = frozenset({Etat.LIVRE, Etat.NON_LIVRE, Etat.EXPIRE, Etat.REJETE})
