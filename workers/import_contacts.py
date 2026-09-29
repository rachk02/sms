"""Worker d'import : lecture en flux, normalisation, dédoublonnage (lot 4)."""

from .base import WorkerEnAttente, lancer


class WorkerImport(WorkerEnAttente):
    nom = "import"
    lot = "4 (T-47)"
    files = ("import.contacts",)


if __name__ == "__main__":
    lancer(WorkerImport)
