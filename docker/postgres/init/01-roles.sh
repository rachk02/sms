#!/bin/bash
# Création des rôles et de la base, au premier démarrage du conteneur seulement.
#
# Trois rôles, aucun super-utilisateur :
#   - le propriétaire possède les tables et lance les migrations. Il reste
#     soumis au cloisonnement, que chaque table force (FORCE ROW LEVEL SECURITY).
#     CREATEDB lui sert à créer la base de test.
#   - le rôle applicatif, utilisé par le service web, soumis au cloisonnement.
#   - le rôle worker, qui contourne le cloisonnement : les workers traitent
#     plusieurs clients à la fois. Seul un super-utilisateur peut poser BYPASSRLS.
#
# Les droits sur les tables sont accordés par défaut, dans template1 et dans la
# base du projet : la base de test, copiée de template1, en hérite. TRUNCATE
# n'est accordé qu'ici, pour la remise à zéro des tests ; la production pose
# ses propres droits.
set -euo pipefail

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres \
    -v proprietaire="$SMS_PROPRIETAIRE_USER" -v proprietaire_mdp="$SMS_PROPRIETAIRE_PASSWORD" \
    -v app="$SMS_APP_USER" -v app_mdp="$SMS_APP_PASSWORD" \
    -v worker="$SMS_WORKER_USER" -v worker_mdp="$SMS_WORKER_PASSWORD" \
    -v base="$SMS_DB" <<'SQL'
CREATE ROLE :"proprietaire" LOGIN PASSWORD :'proprietaire_mdp'
    NOSUPERUSER CREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE :"app" LOGIN PASSWORD :'app_mdp'
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
CREATE ROLE :"worker" LOGIN PASSWORD :'worker_mdp'
    NOSUPERUSER NOCREATEDB NOCREATEROLE BYPASSRLS;
CREATE DATABASE :"base" OWNER :"proprietaire";
SQL

for base in template1 "$SMS_DB"; do
    psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$base" \
        -v proprietaire="$SMS_PROPRIETAIRE_USER" -v app="$SMS_APP_USER" \
        -v worker="$SMS_WORKER_USER" <<'SQL'
ALTER DEFAULT PRIVILEGES FOR ROLE :"proprietaire"
    GRANT SELECT, INSERT, UPDATE, DELETE, TRUNCATE ON TABLES TO :"app", :"worker";
ALTER DEFAULT PRIVILEGES FOR ROLE :"proprietaire"
    GRANT USAGE, SELECT ON SEQUENCES TO :"app", :"worker";
SQL
done
