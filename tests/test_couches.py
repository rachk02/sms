"""L'ordre des couches de la charte, vérifié sur le code.

Un module n'importe que ce qui est sous lui. Le test lit les imports de chaque
module (hors tests et migrations) et refuse tout import qui remonte ou qui reste
dans la même couche. Dans la première couche, `comptes` s'appuie sur `core`,
jamais l'inverse.
"""

import ast
import pathlib

RANGS = {
    "core": 0,
    "comptes": 1,
    "telecom": 2,
    "conformite": 2,
    "fichiers": 2,
    "journal": 3,
    "credits": 3,
    "expediteurs": 3,
    "contacts": 3,
    "campagnes": 4,
    "paiements": 4,
    "notifications": 5,
    "support": 5,
    "api": 6,
    "backoffice": 6,
}
RACINE = pathlib.Path(__file__).resolve().parent.parent / "apps"


def _imports(fichier: pathlib.Path):
    for noeud in ast.walk(ast.parse(fichier.read_text(encoding="utf-8"))):
        if isinstance(noeud, ast.Import):
            yield from (alias.name for alias in noeud.names)
        elif isinstance(noeud, ast.ImportFrom) and noeud.module and noeud.level == 0:
            yield noeud.module


def test_chaque_module_de_apps_a_sa_couche():
    modules = {d.name for d in RACINE.iterdir() if (d / "apps.py").exists()}
    assert modules <= set(RANGS), f"module sans couche : {modules - set(RANGS)}"


def test_aucun_import_ne_remonte_ni_ne_reste_dans_sa_couche():
    fautes = []
    for fichier in RACINE.rglob("*.py"):
        parties = fichier.relative_to(RACINE).parts
        if "tests" in parties or "migrations" in parties:
            continue
        module = parties[0]
        for nom in _imports(fichier):
            cible = nom.split(".")[1] if nom.startswith("apps.") else None
            if cible and cible != module and RANGS[cible] >= RANGS[module]:
                fautes.append(f"{fichier.relative_to(RACINE)} importe {nom}")
    assert not fautes, "\n".join(fautes)
