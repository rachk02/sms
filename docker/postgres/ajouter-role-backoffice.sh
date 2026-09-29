#!/bin/bash
# Crée le rôle du back-office sur un volume PostgreSQL déjà initialisé, où les
# scripts d'init ne se rejouent pas. Sans effet s'il existe déjà.
set -euo pipefail
# Git Bash sous Windows convertirait les chemins du conteneur en chemins Windows.
export MSYS_NO_PATHCONV=1
cd "$(dirname "$0")/../.."
set -a; source .env; set +a
docker compose exec -T \
    -e SMS_BACKOFFICE_USER="$POSTGRES_BACKOFFICE_USER" \
    -e SMS_BACKOFFICE_PASSWORD="$POSTGRES_BACKOFFICE_PASSWORD" \
    postgres bash /docker-entrypoint-initdb.d/02-backoffice.sh
for base in sms_recette; do
    docker compose exec -T postgres psql -qU "$POSTGRES_ADMIN_USER" -d postgres -tc \
        "SELECT 1 FROM pg_database WHERE datname = '$base'" | grep -q 1 || continue
    docker compose exec -T -e SMS_DB="$base" \
        -e SMS_BACKOFFICE_USER="$POSTGRES_BACKOFFICE_USER" \
        -e SMS_BACKOFFICE_PASSWORD="$POSTGRES_BACKOFFICE_PASSWORD" \
        postgres bash /docker-entrypoint-initdb.d/02-backoffice.sh
done
echo "Rôle $POSTGRES_BACKOFFICE_USER prêt."
