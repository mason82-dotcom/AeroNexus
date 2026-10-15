#!/usr/bin/env bash
# Import DJI media + PPK raw data from an SD card (mapping-tool/service/app/sdimport.py).
#   sudo mount -o ro,uid=$(id -u),gid=$(id -g) /dev/sdX1 /mnt/sd     (read-only, checked below)
#   ./sd-import.sh                       inventory: per flight folder what is new, from which drone
#   ./sd-import.sh --import              import new media and the PPK raw data
#   ./sd-import.sh --import --include-ext mp4    also types listed in PILOT_SKIP_UPLOAD_EXT
#   SD_MOUNT=/media/card ./sd-import.sh  other mount point (default /mnt/sd)
# Log of every run: edge/logs/sd-import/<date>-<volume>.jsonl
set -euo pipefail
cd "$(dirname "$0")"
EDGE="$(pwd)"
[ -f .env ] || { echo "ERROR: .env missing"; exit 1; }
set -a; source .env; set +a

MOUNT="${SD_MOUNT:-/mnt/sd}"
src="$(findmnt -rno SOURCE "$MOUNT" || true)"
[ -n "$src" ] || { echo "ERROR: nothing mounted at $MOUNT"; exit 1; }
findmnt -rno OPTIONS "$MOUNT" | tr ',' '\n' | grep -qx ro || {
  echo "ERROR: $MOUNT is not mounted read-only (mount -o ro,...)"; exit 1; }
volume="$(lsblk -no UUID "$src" | head -1)"
[ -n "$volume" ] || { echo "ERROR: no volume id for $src"; exit 1; }

container=aeronexus-edge-mapping-1
image="$(docker inspect -f '{{.Config.Image}}' "$container")"
network="$(docker inspect -f '{{range $k, $v := .NetworkSettings.Networks}}{{$k}} {{end}}' "$container" | awk '{print $1}')"
logdir="$EDGE/logs/sd-import"
mkdir -p "$logdir"
log="$(date +%F)-$volume.jsonl"

echo "== card $volume ($src at $MOUNT), image $image"
exec docker run --rm --network "$network" --user "$(id -u):$(id -g)" \
  -v "$MOUNT:/sd:ro" -v "$logdir:/work" -w /srv \
  -e MAPPING_DB_PASSWORD -e WEB_ADMIN_PASSWORD -e MINIO_STS_USER -e MINIO_STS_PASSWORD -e MINIO_BUCKET \
  -e PILOT_SKIP_UPLOAD_EXT \
  --entrypoint python3 "$image" -m app.sdimport --volume "$volume" --log "/work/$log" "$@"
