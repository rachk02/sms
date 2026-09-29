#!/bin/bash
# Sauvegarde complète de la base, au format personnalisé de pg_dump.
#
#   scripts/sauvegarder.sh [dossier]      dossier par défaut : sauvegardes/
#   BASE=sms_recette scripts/sauvegarder.sh    une autre base que celle de .env
#
# Le vidage passe par le super-utilisateur du conteneur : les tables forcent la
# sécurité par ligne, si bien que même leur propriétaire n'en voit pas les
# lignes, et pg_dump refuse de vider une table filtrée. En production, un rôle
# de sauvegarde dédié, doté de BYPASSRLS et du seul droit de lecture, le
# remplace. Les copies de plus de CONSERVATION_JOURS jours sont supprimées.
set -euo pipefail
cd "$(dirname "$0")/.."
BASE_DEMANDEE="${BASE:-}"
set -a; source .env; set +a
BASE="${BASE_DEMANDEE:-$POSTGRES_DB}"

DOSSIER="${1:-sauvegardes}"
CONSERVATION_JOURS="${CONSERVATION_JOURS:-14}"
mkdir -p "$DOSSIER"
FICHIER="$DOSSIER/${BASE}-$(date -u +%Y%m%dT%H%M%SZ).dump"

docker compose exec -T postgres \
    pg_dump -U "$POSTGRES_ADMIN_USER" -d "$BASE" --format=custom --compress=6 \
    > "$FICHIER.partiel"
mv "$FICHIER.partiel" "$FICHIER"

find "$DOSSIER" -name "${BASE}-*.dump" -mtime +"$CONSERVATION_JOURS" -delete
echo "$FICHIER"
