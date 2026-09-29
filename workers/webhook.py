"""Worker webhook : notifications signées et courriels sortants (lot 9)."""

from .base import WorkerEnAttente, lancer


class WorkerWebhook(WorkerEnAttente):
    nom = "webhook"
    lot = "9 (T-111)"
    files = ("webhooks.sortants", "courriels.sortants")


if __name__ == "__main__":
    lancer(WorkerWebhook)
