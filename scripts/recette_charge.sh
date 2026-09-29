#!/bin/bash
# Point de contrôle du lot 1, en recette : N messages à travers la chaîne.
#
#   scripts/recette_charge.sh [nombre]      par défaut : 10000
set -euo pipefail
cd "$(dirname "$0")/.."
export SMS_CONNEXION_PAR_DEFAUT=WORKER DJANGO_SETTINGS_MODULE=config.settings.recette
uv run python manage.py recette_charge --messages "${1:-10000}"
