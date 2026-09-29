#!/bin/bash
# Démarrage de Jasmin : complète sa configuration depuis l'environnement, puis
# passe la main au point d'entrée de l'image. L'image ne sait régler que l'hôte
# et le port de RabbitMQ et de Redis ; il faut aussi le compte et l'hôte
# virtuel RabbitMQ, que RabbitMQ 4 refuse de laisser à guest hors du poste
# local, et une base Redis propre à Jasmin, pour que ses clés ne se mêlent pas
# à celles de la plateforme.
set -euo pipefail
CFG="${CONFIG_PATH:-/etc/jasmin}/jasmin.cfg"

regler() {  # regler <section> <clé> <valeur>
    python3 - "$CFG" "$1" "$2" "$3" <<'PY'
import configparser, sys
chemin, section, cle, valeur = sys.argv[1:]
config = configparser.RawConfigParser()
config.read(chemin)
if not config.has_section(section):
    config.add_section(section)
config.set(section, cle, valeur)
with open(chemin, "w") as fichier:
    config.write(fichier)
PY
}

regler amqp-broker username "$AMQP_BROKER_USER"
regler amqp-broker password "$AMQP_BROKER_PASSWORD"
regler amqp-broker vhost "${AMQP_BROKER_VHOST:-/}"
regler redis-client dbid "${REDIS_CLIENT_DBID:-2}"
regler jcli bind 0.0.0.0
# Identifiants de la console d'administration. Jasmin 0.11 compare le mot de
# passe à son condensat MD5 : c'est sa façon de faire, pas un choix du projet.
regler jcli admin_username "$JCLI_USER"
regler jcli admin_password "$(python3 -c 'import hashlib, os; print(hashlib.md5(os.environ["JCLI_PASSWORD"].encode()).hexdigest())')"

exec /docker-entrypoint.sh jasmind.py --enable-interceptor-client --enable-dlr-thrower \
    --enable-dlr-lookup -u "$JCLI_USER" -p "$JCLI_PASSWORD"
