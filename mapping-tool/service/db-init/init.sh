#!/bin/sh
# Creates/updates the MySQL user of the mapping service (runs on every start, idempotent).
# Needed because docker-entrypoint-initdb.d only runs on an empty MySQL data directory.
# Rights: everything on database 'mapping', read-only on the two DJI tables the service uses.
set -eu
: "${MYSQL_ROOT_PASSWORD:?}" "${MAPPING_DB_PASSWORD:?}"
case "$MAPPING_DB_PASSWORD" in *"'"*|*'\'*) echo "MAPPING_DB_PASSWORD must not contain ' or \\"; exit 1;; esac

mysql -h mysql -uroot -p"$MYSQL_ROOT_PASSWORD" <<SQL
CREATE DATABASE IF NOT EXISTS \`mapping\` DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_general_ci;
CREATE USER IF NOT EXISTS 'mapping'@'%' IDENTIFIED BY '${MAPPING_DB_PASSWORD}';
ALTER USER 'mapping'@'%' IDENTIFIED BY '${MAPPING_DB_PASSWORD}';
GRANT ALL PRIVILEGES ON \`mapping\`.* TO 'mapping'@'%';
GRANT SELECT ON \`cloud_sample\`.\`media_file\` TO 'mapping'@'%';
GRANT SELECT ON \`cloud_sample\`.\`wayline_file\` TO 'mapping'@'%';
FLUSH PRIVILEGES;
SQL
echo "mapping-db-init done"
