#!/usr/bin/env bash
# AeroNexus Edge restore from BACKUP_DIR (see backup.sh).
#   ./restore.sh --test            restore the newest backup into THROWAWAY containers and compare it with the
#                                  live stack (row counts per table, objects + bytes per bucket). Touches nothing live.
#   ./restore.sh --yes             restore the newest backup INTO THE LIVE STACK (MySQL databases are replaced,
#                                  MinIO objects from the backup are written back; objects not in the backup stay)
#   --dump FILE                    use this .sql.gz instead of the newest one
# Disaster recovery on a fresh machine: see edge/README.md, section "Datensicherung".
set -euo pipefail
cd "$(dirname "$0")"
[ -f .env ] || { echo "ERROR: .env missing (copy BACKUP_DIR/config/env-<stamp> to edge/.env)"; exit 1; }
set -a; source .env; set +a
BACKUP_DIR="${BACKUP_DIR:-/mnt/hc4backup/aeronexus}"
NET=aeronexus-edge_default
# same MySQL version as the live stack (override with MYSQL_IMAGE=... to test an upgrade)
MYSQL_IMAGE="${MYSQL_IMAGE:-$(sed -n 's/^ *image: \(mysql:[0-9.]*\).*/\1/p' docker-compose.yml | head -1)}"
MINIO_IMAGE=aeronexus/minio:RELEASE.2025-10-15T17-29-55Z

mode="" dump=""
while [ $# -gt 0 ]; do
  case "$1" in
    --test) mode=test ;;
    --yes) mode=live ;;
    --dump) dump="$2"; shift ;;
    *) echo "usage: $0 --test | --yes [--dump FILE]"; exit 1 ;;
  esac
  shift
done
[ -n "$mode" ] || { echo "usage: $0 --test | --yes [--dump FILE]"; exit 1; }
[ -n "$dump" ] || dump="$(ls -1 "$BACKUP_DIR"/mysql/aeronexus-*.sql.gz 2>/dev/null | tail -1)"
[ -f "$dump" ] || { echo "ERROR: no dump found in $BACKUP_DIR/mysql"; exit 1; }
echo "dump:  $dump"
echo "mysql: $MYSQL_IMAGE"
echo "minio: $BACKUP_DIR/minio"

# mc in a one-shot container on the compose network; aliases: live, test, local dir /backup
mc_run () {
  docker run --rm -i --network "$NET" --user "$(id -u):$(id -g)" -e MC_CONFIG_DIR=/tmp/mc \
    -e "MC_HOST_live=http://$MINIO_ROOT_USER:$MINIO_ROOT_PASSWORD@minio:9000" \
    -e "MC_HOST_test=http://restoretest:restoretest123@aeronexus-restoretest-minio:9000" \
    -v "$BACKUP_DIR/minio:/backup:ro" --entrypoint sh "$MINIO_IMAGE" -c "$1"
}

# "table rows" for every table of the backed-up databases, exact counts
row_counts () {  # docker exec prefix (with MYSQL_PWD set) ...
  local q
  q="$("$@" mysql -uroot -N -e "SELECT CONCAT('SELECT ''', table_schema, '.', table_name, ''', COUNT(*) FROM \`',
        table_schema, '\`.\`', table_name, '\`;') FROM information_schema.tables
        WHERE table_schema IN ('cloud_sample','mapping') AND table_type='BASE TABLE' ORDER BY 1")"
  echo "$q" | "$@" mysql -uroot -N | sort
}

bucket_stats () {  # alias
  mc_run "for b in \$(mc ls --json $1 | sed -n 's/.*\"key\":\"\\([^\"]*\\)\\/\".*/\\1/p'); do
            mc ls -r --json $1/\$b | sed -n 's/.*\"size\":\\([0-9]*\\).*/\\1/p' |
              awk -v b=\$b '{n++; s+=\$1} END {printf \"%s %d objects %d bytes\\n\", b, n, s}'
          done" | sort
}

if [ "$mode" = test ]; then
  cleanup () { docker rm -f aeronexus-restoretest-mysql aeronexus-restoretest-minio >/dev/null 2>&1 || true; }
  trap cleanup EXIT
  cleanup
  echo "== starting throwaway MySQL + MinIO"
  docker run -d --name aeronexus-restoretest-mysql --network "$NET" -e MYSQL_ROOT_PASSWORD=restoretest \
    --tmpfs /var/lib/mysql "$MYSQL_IMAGE" >/dev/null
  docker run -d --name aeronexus-restoretest-minio --network "$NET" --tmpfs /data \
    -e MINIO_ROOT_USER=restoretest -e MINIO_ROOT_PASSWORD=restoretest123 "$MINIO_IMAGE" server /data >/dev/null
  # the entrypoint first runs a socket-only init server, then restarts: wait for TCP
  for _ in $(seq 90); do
    docker exec -e MYSQL_PWD=restoretest aeronexus-restoretest-mysql mysql -h127.0.0.1 -uroot -e "SELECT 1" >/dev/null 2>&1 && break
    sleep 2
  done
  echo "== loading dump"
  zcat "$dump" | docker exec -i -e MYSQL_PWD=restoretest aeronexus-restoretest-mysql mysql -h127.0.0.1 -uroot
  echo "== mirroring backup into the test MinIO"
  mc_run 'set -e; for d in /backup/*/; do b=$(basename "$d"); mc mb -q "test/$b" >/dev/null; mc mirror -q "$d" "test/$b" >/dev/null; done'

  echo "== comparing (live stack vs. restored copy)"
  live_rows="$(row_counts docker compose exec -T -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql)"
  test_rows="$(row_counts docker exec -i -e MYSQL_PWD=restoretest aeronexus-restoretest-mysql)"
  live_obj="$(bucket_stats live)"
  test_obj="$(bucket_stats test)"
  rc=0
  if [ "$live_rows" = "$test_rows" ]; then
    echo "ok   MySQL: $(echo "$test_rows" | wc -l) tables, $(echo "$test_rows" | awk '{s+=$2} END {print s}') rows identical"
  else
    echo "DIFF MySQL (live < > restored; changes since the dump are expected):"
    diff <(echo "$live_rows") <(echo "$test_rows") | sed 's/^/     /' || true; rc=1
  fi
  # buckets that are empty live have no directory in the mirror -> compare non-empty buckets only
  live_obj="$(echo "$live_obj" | grep -v ' 0 objects' || true)"
  if [ "$live_obj" = "$test_obj" ]; then
    echo "ok   MinIO:"; echo "$test_obj" | sed 's/^/     /'
  else
    echo "DIFF MinIO (live < > restored; objects deleted live stay in the mirror):"
    diff <(echo "$live_obj") <(echo "$test_obj") | sed 's/^/     /' || true; rc=1
  fi
  exit $rc
fi

# ---- live restore ----
echo "== stopping services that write to MySQL/MinIO"
docker compose stop api mapping farming
echo "== restoring MySQL databases from $(basename "$dump")"
zcat "$dump" | docker compose exec -T -e MYSQL_PWD="$MYSQL_ROOT_PASSWORD" mysql mysql -uroot
echo "== restoring MinIO objects"
mc_run 'set -e; for d in /backup/*/; do b=$(basename "$d"); mc mb -q --ignore-existing "live/$b"; mc mirror -q --overwrite "$d" "live/$b" >/dev/null; echo "  $b"; done'
echo "== starting services"
docker compose up -d
echo "restore done"
