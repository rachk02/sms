"""Documentation de l'API issue du code (T-110).

La documentation ne peut pas diverger du code : chaque route publique a sa
fiche et chaque fiche sa route, avec les méthodes de la vue ; chaque code
d'erreur rendu par le code est documenté, et aucun code documenté n'est
inventé ; le texte d'en-tête est celui de la politique de version. Chaque
route porte un exemple complet dans les quatre langages.
"""

import inspect
import json
import re
from pathlib import Path

import pytest

from apps.api import auth, documentation, views
from apps.api.models import PointReception
from apps.campagnes import services as campagnes
from apps.core.etats import Etat

RACINE = Path(__file__).resolve().parents[3]


def test_chaque_route_publique_a_sa_fiche_et_chaque_fiche_sa_route():
    routes = documentation.routes_publiques()
    fiches = {fiche.nom for fiche in documentation.FICHES}
    assert fiches == set(routes)
    from apps.api import urls

    vues = {m.name: m.callback for m in urls.urlpatterns if m.name in routes}
    for nom, vue in vues.items():
        documentees = {f.methode for f in documentation.FICHES if f.nom == nom}
        assert documentees == set(vue.methodes), nom


def _codes_rendus(module) -> set[str]:
    source = inspect.getsource(module)
    return set(re.findall(r'erreur\(\s*\d{3},\s*"([A-Z_]+)"', source)) | set(
        re.findall(r'"code": "([A-Z_]+)"', source)
    )


def test_chaque_code_d_erreur_rendu_est_documente_et_aucun_n_est_invente():
    rendus = _codes_rendus(views) | _codes_rendus(auth)
    assert rendus == set(documentation.ERREURS)
    # Le code HTTP documenté est celui que le code rend.
    for module in (views, auth):
        for statut, code in re.findall(
            r'erreur\(\s*(\d{3}),\s*"([A-Z_]+)"', inspect.getsource(module)
        ):
            assert documentation.ERREURS[code][0] == int(statut), code


def test_chaque_refus_d_envoi_est_documente():
    source = inspect.getsource(campagnes)
    rendus = set(re.findall(r'envoi\.code(?:, envoi\.motif)? = "([A-Z_]+)"', source))
    assert rendus == set(documentation.REFUS)


def test_le_bac_a_sable_documente_suit_le_code():
    from apps.api.essais import ISSUES

    # Le tableau des issues écrit dans le texte est celui du code.
    for chiffres, issue in (
        ("0 à 6", "LIVRE"),
        ("7", "NON_LIVRE"),
        ("8", "EXPIRE"),
        ("9", "SANS_ACCUSE"),
    ):
        premier = chiffres[0]
        assert ISSUES[premier] == issue
        assert chiffres in documentation.BAC_A_SABLE
    assert {ISSUES[c] for c in "0123456"} == {"LIVRE"}


def test_les_etats_et_les_evenements_suivent_le_code():
    assert set(documentation.ETATS) == set(Etat.values)
    assert set(documentation.EVENEMENTS) == set(PointReception.Evenement.values)


def test_le_texte_d_en_tete_est_celui_de_la_politique_de_version():
    politique = (RACINE / "docs" / "api" / "politique-de-version.md").read_text(encoding="utf-8")
    citation = " ".join(
        ligne.removeprefix(">").strip() for ligne in politique.splitlines() if ligne.startswith(">")
    )
    citation = re.sub(r"\s+", " ", citation)
    for regle in documentation.REGLES_INTEGRATEUR:
        assert regle in citation, regle[:60]


def test_chaque_route_porte_un_exemple_complet_dans_les_quatre_langages():
    base = "https://api.exemple.bf"
    for fiche in documentation.FICHES:
        exemples = documentation.exemples(fiche, base)
        assert set(exemples) == {"cURL", "PHP", "Python", "JavaScript"}
        adresse = base + documentation.chemin(fiche)
        for nom, valeur in fiche.chemin_exemple.items():
            adresse = adresse.replace("{" + nom + "}", valeur)
        for langage, code in exemples.items():
            assert adresse in code, (fiche.nom, langage)
            assert documentation.CLE_EXEMPLE in code, (fiche.nom, langage)
            assert fiche.methode in code.upper(), (fiche.nom, langage)
            if fiche.corps:
                assert "commande-42" in code, (fiche.nom, langage)
        assert fiche.reponse, fiche.nom


def test_le_schema_openapi_decrit_chaque_route_et_ses_erreurs():
    schema = documentation.openapi("https://api.exemple.bf")
    assert schema["openapi"] == "3.1.0" and schema["info"]["version"] == auth.VERSION
    for fiche in documentation.FICHES:
        operation = schema["paths"][documentation.chemin(fiche)][fiche.methode.lower()]
        assert str(fiche.statut) in operation["responses"]
        for code in (*fiche.erreurs, *documentation.ERREURS_COMMUNES):
            statut = str(documentation.ERREURS[code][0])
            assert code in operation["responses"][statut]["description"]
    assert set(schema["webhooks"]) == set(PointReception.Evenement.values)
    json.dumps(schema)


@pytest.mark.django_db
def test_la_documentation_et_le_schema_se_lisent_sans_compte(client):
    schema = client.get("/v1/openapi.json")
    assert schema.status_code == 200
    assert schema.json()["paths"].keys() == documentation.openapi()["paths"].keys()
    page = client.get("/documentation/api")
    assert page.status_code == 200
    contenu = page.content.decode()
    for fiche in documentation.FICHES:
        assert documentation.chemin(fiche) in contenu
    for code in documentation.ERREURS:
        assert code in contenu
