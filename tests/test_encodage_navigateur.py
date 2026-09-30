"""Le compteur de la page et celui du serveur donnent le même résultat (T-57).

`static/js/encodage.js` est exécuté par node sur les tables que fournit
`apps.core.encodage`, et comparé à `analyser` et `convertir` sur des textes
choisis et sur des milliers de textes tirés au hasard. Node est présent sur
les postes et sur les machines de l'intégration continue ; son absence est une
erreur, pas un test sauté.
"""

import json
import random
import shutil
import subprocess
from pathlib import Path

from apps.core.encodage import (
    ALPHABET_GSM,
    CONVERSIONS,
    EXTENSION_GSM,
    analyser,
    convertir,
    table_pour_le_navigateur,
)

SCRIPT = Path(__file__).resolve().parent.parent / "static" / "js" / "encodage.js"

HARNAIS = """
require(process.argv[1]);
let entree = "";
process.stdin.setEncoding("utf8");
process.stdin.on("data", (morceau) => (entree += morceau));
process.stdin.on("end", () => {
  const { table, textes } = JSON.parse(entree);
  const moteur = globalThis.EncodageSms.creer(table);
  const resultats = textes.map((texte) => {
    const analyse = moteur.analyser(texte);
    return [analyse.encodage, analyse.unites, analyse.segments, analyse.capacite,
            analyse.hors_gsm, moteur.convertir(texte)];
  });
  process.stdout.write(JSON.stringify(resultats));
});
"""


def _navigateur(textes: list[str]) -> list:
    node = shutil.which("node")
    assert node, "node est requis pour vérifier le compteur de la page"
    sortie = subprocess.run(
        [node, "-e", HARNAIS, str(SCRIPT)],
        input=json.dumps({"table": table_pour_le_navigateur(), "textes": textes}),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    return json.loads(sortie.stdout)


def _serveur(texte: str) -> list:
    analyse = analyser(texte)
    capacite = 160 if analyse.encodage == "GSM7" else 70
    return [
        str(analyse.encodage),
        analyse.unites,
        analyse.segments,
        capacite,
        sorted(analyse.hors_gsm),
        convertir(texte),
    ]


def test_un_accent_circonflexe_fait_tomber_la_capacite_de_160_a_70():
    gsm, ucs2 = _navigateur(["Bonne fete a tous", "Bonne fête à tous"])
    assert (gsm[0], gsm[3], gsm[4]) == ("GSM7", 160, [])
    assert (ucs2[0], ucs2[3], ucs2[4]) == ("UCS2", 70, ["ê"])
    # La conversion en un clic ne touche que le caractère coûteux.
    assert ucs2[5] == "Bonne fete à tous"


def test_les_accents_de_la_table_gsm_ne_declenchent_rien():
    (resultat,) = _navigateur(["éèùìòàÉÇäöüñÄÖÜÑ Noel"])
    assert resultat[0] == "GSM7" and resultat[4] == []


def test_la_conversion_ne_remplace_que_des_caracteres_couteux_par_du_gsm():
    for caractere, equivalent in CONVERSIONS.items():
        assert caractere not in ALPHABET_GSM and caractere not in EXTENSION_GSM, caractere
        assert analyser(equivalent).encodage == "GSM7", caractere


def test_la_page_et_le_serveur_comptent_pareil():
    choisis = [
        "",
        "a" * 160,
        "a" * 161,
        "é" * 306,
        "€" * 80,
        "€" * 81,
        "x" * 152 + "€",
        "ê" * 70,
        "ê" * 71,
        "Promo \U0001f600 " * 20,
        f"Réunion à 15 h {chr(0x2014)} « salle B » {chr(0x2026)}",
    ]
    hasard = random.Random(57)
    reserve = "aZ09 .,é€[{ê’«\U0001f600\nçœ" + "".join(sorted(ALPHABET_GSM))[:40]
    tires = ["".join(hasard.choices(reserve, k=hasard.randint(0, 400))) for _ in range(3000)]
    textes = choisis + tires
    for texte, page in zip(textes, _navigateur(textes), strict=True):
        page[4] = sorted(page[4])
        assert page == _serveur(texte), texte


def test_le_script_de_l_editeur_se_lit_sans_erreur_de_syntaxe():
    node = shutil.which("node")
    assert node, "node est requis pour vérifier les scripts de la page"
    subprocess.run([node, "--check", str(SCRIPT.with_name("compositeur.js"))], check=True)
