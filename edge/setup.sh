#!/usr/bin/env bash
# AeroNexus Edge setup:
# clones the upstream DJI demos at pinned commits, applies the AeroNexus patches
# (api/patches, control/patches), copies build files and renders secret-bearing files from .env.
# Everything generated lands in edge/runtime/ (gitignored).
set -euo pipefail
cd "$(dirname "$0")"
ROOT="$(cd .. && pwd)"
RT=runtime

BACKEND_REPO=https://github.com/dji-sdk/DJI-Cloud-API-Demo.git
BACKEND_COMMIT=bef525cb92772b06786c1e033719a6fa1b94bcc5   # last upstream commit, 2025-04-10
WEB_REPO=https://github.com/dji-sdk/Cloud-API-Demo-Web.git
WEB_COMMIT=bfcf3ee998b876dc5d372e5dbac913a19ccdac9f       # last upstream commit, 2025-04-10

[ -f .env ] || { echo "ERROR: .env missing -> cp .env.example .env and fill it in"; exit 1; }
set -a; source .env; set +a

for v in SERVER_HOST DJI_APP_ID DJI_APP_KEY DJI_APP_LICENSE MYSQL_ROOT_PASSWORD; do
  [ -n "${!v:-}" ] || { echo "ERROR: $v is empty in .env"; exit 1; }
done
grep -q "change-me" .env && echo "WARNING: .env still contains change-me values"

clone_and_patch () {  # dir repo commit patchdir
  local dir=$1 repo=$2 commit=$3 patches=$4
  if [ -d "$dir/.git" ]; then
    echo "== $dir exists, skipping clone (delete runtime/upstream to re-apply patches)"
    return
  fi
  git clone -q "$repo" "$dir"
  git -C "$dir" checkout -q "$commit"
  git -C "$dir" -c user.name=aeronexus -c user.email=aeronexus@local am -q "$patches"/*.patch
  echo "== $dir @ ${commit:0:7} + $(ls "$patches"/*.patch | wc -l) patch(es)"
}

mkdir -p $RT/upstream $RT/initdb $RT/emqx data/mysql data/redis data/emqx data/minio logs
clone_and_patch $RT/upstream/backend "$BACKEND_REPO" "$BACKEND_COMMIT" "$ROOT/api/patches"
clone_and_patch $RT/upstream/web     "$WEB_REPO"     "$WEB_COMMIT"     "$ROOT/control/patches"

cp "$ROOT/api/docker/Dockerfile"     $RT/upstream/backend/Dockerfile
cp "$ROOT/control/docker/Dockerfile" $RT/upstream/web/Dockerfile
cp "$ROOT/control/docker/nginx.conf" $RT/upstream/web/nginx.conf

# MySQL init order: 01 upstream schema, 02 fleet dictionary, 03 credentials
cp $RT/upstream/backend/sql/cloud_sample.sql $RT/initdb/01_cloud_sample.sql
cp "$ROOT"/fleet/sql/*.sql $RT/initdb/
perl -pe 's/\$\{(\w+)\}/$ENV{$1}/g' 03_credentials.sql.template > $RT/initdb/03_credentials.sql

cat > $RT/emqx/auth-bootstrap.csv <<CSV
user_id,password,is_superuser
${MQTT_SERVER_USER},${MQTT_SERVER_PASSWORD},true
${MQTT_PILOT_USER},${MQTT_PILOT_PASSWORD},false
CSV

if [ -d data/mysql/mysql ]; then
  echo "NOTE: data/mysql already initialised -> SQL files are NOT re-applied."
  echo "      Apply runtime/initdb/02_*.sql and 03_*.sql manually or wipe data/mysql."
fi

cat <<MSG

Setup done. Next:
  docker compose up -d --build
  Web UI:            http://${SERVER_HOST}:8085          (login: adminPC)
  Pilot 2 login URL: http://${SERVER_HOST}:8085/pilot-login (login: pilot)
MSG
