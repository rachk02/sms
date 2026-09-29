#!/bin/bash
# Rôle du back-office : les agents EL-IMMEKA n'appartiennent à aucun client et
# doivent voir tous les comptes pour enregistrer, revoir et suspendre. Le service
# du back-office, joignable depuis le réseau interne seul, se connecte avec ce
# rôle, qui contourne le cloisonnement ; le service web des clients ne l'a pas.
#
# Idempotent : exécuté au premier démarrage du conteneur, et à la main sur un
# volume existant (voir docker/postgres/ajouter-role-backoffice.sh).
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v role="$SMS_BACKOFFICE_USER" -v mdp="$SMS_BACKOFFICE_PASSWORD" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS', :'role')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'role') \gexec
ALTER ROLE :"role" PASSWORD :'mdp';
SQL

for base in template1 "$SMS_DB"; do
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$base" \
        -v proprietaire="$SMS_PROPRIETAIRE_USER" -v role="$SMS_BACKOFFICE_USER" <<'SQL'
ALTER DEFAULT PRIVILEGES FOR ROLE :"proprietaire"
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO :"role";
ALTER DEFAULT PRIVILEGES FOR ROLE :"proprietaire"
    GRANT USAGE, SELECT ON SEQUENCES TO :"role";
GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON ALL TABLES IN SCHEMA public TO :"role";
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO :"role";
SQL
done
