#!/usr/bin/env bash
# PPK camera positions for a flight set (mapping-tool/service/app/ppk.py, RTKLIB demo5 in the mapping image).
#   ./ppk.sh <flight set id> <base dir>                    report + geo.txt (edge/logs/ppk/ppk-<set>/)
#   ./ppk.sh <flight set id> <base dir> --create-job [--name "..."]   also a mapping job using the positions
# <base dir>: reference station data covering the flight (SAPOS GPPS/VRS, EPN highrate, own base): RINEX 2/3,
# Hatanaka .crx, .gz or BKG .tar.gz day archives (only the files covering the flight are unpacked).
# The flight must come from an SD import (edge/sd-import.sh): its PPK raw data (.OBS/.NAV/.MRK) is used.
set -euo pipefail
cd "$(dirname "$0")"
EDGE="$(pwd)"
[ $# -ge 2 ] || { sed -n '2,8p' "$0"; exit 1; }
set_id="$1"; base="$(realpath "$2")"; shift 2
[ -d "$base" ] || { echo "ERROR: $base is not a directory"; exit 1; }
[ -f .env ] || { echo "ERROR: .env missing"; exit 1; }
set -a; source .env; set +a

container=aeronexus-edge-mapping-1
image="$(docker inspect -f '{{.Config.Image}}' "$container")"
network="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$container" | awk '{print $1}')"
workdir="$EDGE/logs/ppk"
mkdir -p "$workdir"

# the running service's own environment (same image: DB, MinIO, buckets) plus the web login for --create-job
exec docker run --rm --network "$network" --user "$(id -u):$(id -g)" \
  --env-file <(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' "$container" | grep -v '^$') \
  -e WEB_ADMIN_PASSWORD \
  -v "$base:/base:ro" -v "$workdir:/work" -w /srv \
  --entrypoint python3 "$image" -m app.ppk --set "$set_id" --base /base --work /work "$@"
