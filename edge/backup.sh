#!/usr/bin/env bash
# AeroNexus Edge backup: MySQL dump, MinIO buckets (mirror) and edge/.env to BACKUP_DIR.
#   BACKUP_DIR/mysql/aeronexus-<stamp>.sql.gz   one per run, older than BACKUP_KEEP_DAYS are removed
#   BACKUP_DIR/config/env-<stamp>               secrets needed for a restore (mode 600, same retention)
#   BACKUP_DIR/minio/<bucket>/...               incremental mirror; files deleted in MinIO are KEPT here
#   BACKUP_DIR/last-success                     timestamp + summary of the last good run (monitoring)
# Runs against the live stack (consistent InnoDB dump via --single-transaction). Restore: ./restore.sh
# Cron (installed by ./backup.sh --install-cron): daily 03:15, output appended to BACKUP_DIR/backup.log
set -euo pipefail
cd "$(dirname "$0")"
EDGE="$(pwd)"

[ -f .env ] || { echo "ERROR: .env missing"; exit 1; }
set -a; source .env; set +a
BACKUP_DIR="${BACKUP_DIR:-/mnt/hc4backup/aeronexus}"
BACKUP_KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
DATABASES="cloud_sample mapping"

if [ "${1:-}" = "--install-cron" ]; then
  line="15 3 * * * $EDGE/backup.sh >> $BACKUP_DIR/backup.log 2>&1"
  # keep all other entries; an empty crontab or no match must not abort (set -e + pipefail)
  { crontab -l 2>/dev/null || true; } | { grep -vF "$EDGE/backup.sh" || true; } > "$BACKUP_DIR/.crontab.new"
  echo "$line" >> "$BACKUP_DIR/.crontab.new"
  crontab "$BACKUP_DIR/.crontab.new" && rm -f "$BACKUP_DIR/.crontab.new"
  echo "cron installed: $line"; exit 0
fi

mkdir -p "$BACKUP_DIR"/{mysql,config,minio}
chmod 700 "$BACKUP_DIR"
exec 9>"$BACKUP_DIR/.lock"
flock -n 9 || { echo "ERROR: another backup is running"; exit 1; }

stamp="$(date +%Y-%m-%d_%H%M)"
log () { echo "$(date '+%F %T') $*"; }
log "backup start -> $BACKUP_DIR"

# 1. MySQL: dump to a temp file, check it is complete, then move into place
dump="$BACKUP_DIR/mysql/aeronexus-$stamp.sql.gz"
docker compose exec -T mysql sh -c \
  "MYSQL_PWD=\"\$MYSQL_ROOT_PASSWORD\" exec mysqldump -uroot --single-transaction --routines --events \
   --triggers --hex-blob --databases $DATABASES" | gzip -6 > "$dump.part"
gzip -t "$dump.part"
zcat "$dump.part" | tail -1 | grep -q "^-- Dump completed" || { echo "ERROR: dump incomplete"; exit 1; }
mv "$dump.part" "$dump"
log "mysql  $(du -h "$dump" | cut -f1)  $(basename "$dump")"

# 2. MinIO: mirror every bucket (mc from the minio-init image, files owned by the calling user)
docker compose run --rm --no-deps -T --user "$(id -u):$(id -g)" \
  -e MC_CONFIG_DIR=/tmp/mc -e "MC_HOST_live=http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@minio:9000" \
  -v "$BACKUP_DIR/minio:/backup" --entrypoint sh minio-init -c '
    set -e
    for b in $(mc ls --json live | sed -n "s/.*\"key\":\"\([^\"]*\)\/\".*/\1/p"); do
      mc mirror --quiet --overwrite --preserve "live/$b" "/backup/$b" >/dev/null
      echo "bucket $b"
    done' | while read -r line; do log "minio  $line"; done
[ "${PIPESTATUS[0]}" -eq 0 ] || { echo "ERROR: minio mirror failed"; exit 1; }
log "minio  $(du -sh "$BACKUP_DIR/minio" | cut -f1) total"

# 3. Config (.env holds every secret; without it the restored data cannot be used)
install -m 600 .env "$BACKUP_DIR/config/env-$stamp"

# 4. Retention (only timestamped files, the MinIO mirror is never pruned)
find "$BACKUP_DIR/mysql" -name 'aeronexus-*.sql.gz' -mtime +"$BACKUP_KEEP_DAYS" -delete
find "$BACKUP_DIR/config" -name 'env-*' -mtime +"$BACKUP_KEEP_DAYS" -delete
find "$BACKUP_DIR/mysql" -name '*.part' -delete

printf '%s\nmysql %s\nminio %s\n' "$(date -Iseconds)" "$(basename "$dump")" \
  "$(du -sh "$BACKUP_DIR/minio" | cut -f1)" > "$BACKUP_DIR/last-success"
log "backup ok"
