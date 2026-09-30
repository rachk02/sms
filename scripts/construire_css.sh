#!/bin/bash
# Construit la feuille de style du portail avec Tailwind, par son binaire
# autonome : aucune chaîne Node n'est nécessaire. Seules les classes présentes
# dans les gabarits sont gardées (purge). La feuille produite est versionnée, si
# bien que lancer la plateforme ne demande aucune construction.
#
#   scripts/construire_css.sh [--surveiller]
set -euo pipefail
cd "$(dirname "$0")/.."
VERSION=v4.3.3
case "$(uname -s)" in
    MINGW*|MSYS*|CYGWIN*) BINAIRE=tailwindcss-windows-x64.exe ;;
    Darwin) BINAIRE=tailwindcss-macos-arm64 ;;
    *) BINAIRE=tailwindcss-linux-x64 ;;
esac
OUTIL=".outils/tailwindcss-$VERSION-$BINAIRE"
if [ ! -x "$OUTIL" ]; then
    mkdir -p .outils
    curl -sfL "https://github.com/tailwindlabs/tailwindcss/releases/download/$VERSION/$BINAIRE" -o "$OUTIL"
    chmod +x "$OUTIL"
fi
"$OUTIL" -i static/css/source.css -o static/css/portail.css --minify ${1:+--watch}
