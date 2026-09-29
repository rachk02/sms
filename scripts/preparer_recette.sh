#!/bin/bash
# Prépare l'environnement de recette : sa base, son hôte virtuel RabbitMQ, ses
# migrations, ses files et son jeu d'essai. Sans effet sur ce qui existe déjà,
# sauf RECREER=oui, qui supprime et recrée la base de recette.
#
#   scripts/preparer_recette.sh
set -euo pipefail
cd "$(dirname "$0")/.."
[ -f .env.recette ] || cp .env.recette.example .env.recette
set -a; source .env; source .env.recette; set +a

psql_admin() { docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -qAt -U "$POSTGRES_ADMIN_USER" -d postgres "$@"; }
if [ "${RECREER:-non}" = "oui" ]; then
    psql_admin -c "DROP DATABASE IF EXISTS \"$POSTGRES_DB\" WITH (FORCE)"
fi
if [ -z "$(psql_admin -c "SELECT 1 FROM pg_database WHERE datname = '$POSTGRES_DB'")" ]; then
    psql_admin -c "CREATE DATABASE \"$POSTGRES_DB\" OWNER \"$POSTGRES_PROPRIETAIRE_USER\""
fi

rabbit() { docker compose exec -T rabbitmq rabbitmqctl -q "$@"; }
rabbit list_vhosts | grep -qx "$RABBITMQ_VHOST" || rabbit add_vhost "$RABBITMQ_VHOST"
rabbit set_permissions -p "$RABBITMQ_VHOST" "$RABBITMQ_USER" ".*" ".*" ".*"

export DJANGO_SETTINGS_MODULE=config.settings.recette
uv run python manage.py migrate --database proprietaire -v 0
uv run python manage.py declarer_files
uv run python manage.py semer_recette
uv run python manage.py verifier_base
