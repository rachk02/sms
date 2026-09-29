"""Refuse tout tiret cadratin et tout emoji dans les fichiers suivis par git.

La règle est dans la charte : ni l'un ni l'autre, nulle part. Un caractère
écrit sous forme de séquence d'échappement dans un test reste permis.
"""

import subprocess
import sys

TIRET_CADRATIN = "—"
PLAGES_EMOJI = (
    (0x1F000, 0x1FAFF),
    (0x2600, 0x27BF),
    (0x2B00, 0x2BFF),
    (0xFE0F, 0xFE0F),
)


def interdit(caractere: str) -> bool:
    if caractere == TIRET_CADRATIN:
        return True
    point = ord(caractere)
    return any(debut <= point <= fin for debut, fin in PLAGES_EMOJI)


def main() -> int:
    fichiers = subprocess.run(
        ["git", "ls-files", "-z"], capture_output=True, check=True, text=True
    ).stdout.split("\0")
    fautes = []
    for chemin in filter(None, fichiers):
        try:
            with open(chemin, encoding="utf-8") as fichier:
                lignes = fichier.readlines()
        except UnicodeDecodeError, FileNotFoundError, IsADirectoryError:
            continue
        for numero, ligne in enumerate(lignes, start=1):
            for caractere in ligne:
                if interdit(caractere):
                    fautes.append(f"{chemin}:{numero}: U+{ord(caractere):04X}")
    for faute in fautes:
        print(faute)
    if fautes:
        print(f"{len(fautes)} caractère(s) interdit(s) : tiret cadratin ou emoji.")
        return 1
    print("Aucun tiret cadratin ni emoji.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
