"""Worker accusés : corrélation, transitions, règlement du crédit (lot 2)."""

from .base import WorkerEnAttente, lancer


class WorkerAccuses(WorkerEnAttente):
    nom = "accuses"
    lot = "2 (T-26, T-27, T-28)"
    files = ("accuses.entrants", "messages.entrants")


if __name__ == "__main__":
    lancer(WorkerAccuses)
