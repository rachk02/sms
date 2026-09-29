#!/bin/bash
# Restaure une sauvegarde dans une base, créée ou recréée pour l'occasion.
#
#   scripts/restaurer.sh <fichier.dump> <base_cible>
#
# La base cible est supprimée si elle existe : ne jamais la faire pointer sur la
# base en service sans l'avoir décidé. Les rôles (propriétaire, applicatif,
# worker) doivent exister sur le serveur cible ; la restauration garde les
# propriétaires et les droits d'origine.
set -euo pipefail
cd "$(dirname "$0")/.."
set -a; source .env; set +a

FICHIER="${1:?fichier de sauvegarde attendu}"
CIBLE="${2:?base cible attendue}"
if [ "$CIBLE" = "$POSTGRES_DB" ] && [ "${RESTAURER_SUR_LA_BASE_EN_SERVICE:-non}" != "oui" ]; then
    echo "Refus : $CIBLE est la base en service. RESTAURER_SUR_LA_BASE_EN_SERVICE=oui pour forcer." >&2
    exit 1
fi

psql_admin() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U "$POSTGRES_ADMIN_USER" "$@"; }

psql_admin -d postgres -c "DROP DATABASE IF EXISTS \"$CIBLE\" WITH (FORCE)"
psql_admin -d postgres -c "CREATE DATABASE \"$CIBLE\" OWNER \"$POSTGRES_PROPRIETAIRE_USER\""
docker compose exec -T postgres \
    pg_restore -U "$POSTGRES_ADMIN_USER" -d "$CIBLE" --exit-on-error < "$FICHIER"
echo "Restauré dans $CIBLE."
