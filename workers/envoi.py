"""Worker d'envoi : dernier contrôle, choix de route, soumission (lot 2).

Deux groupes au moins en exploitation, l'un sur les files prioritaires, l'autre
sur les files de masse, pour qu'un code à usage unique ne passe jamais derrière
une campagne. SMS_FILES_ENVOI choisit les files d'un processus.
"""

import os

from apps.core.files import FILES

from .base import WorkerEnAttente, lancer


class WorkerEnvoi(WorkerEnAttente):
    nom = "envoi"
    lot = "2 (T-24)"
    files = tuple(
        os.environ.get("SMS_FILES_ENVOI", "").split(",")
        if os.environ.get("SMS_FILES_ENVOI")
        else [f for f in FILES if f.startswith("envoi.")]
    )


if __name__ == "__main__":
    lancer(WorkerEnvoi)
