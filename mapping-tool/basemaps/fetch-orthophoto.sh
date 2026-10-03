#!/usr/bin/env bash
# Offline orthophotos (LGL BW DOP20, open data dl-de/by-2-0) -> edge/data/basemaps/<name>.pmtiles
#   ./fetch-orthophoto.sh                       circle of 5 km around HOME_POINT (edge/.env), name luftbild-home
#   ./fetch-orthophoto.sh <name> <radius_km>    circle around HOME_POINT
#   ./fetch-orthophoto.sh <name> regions/x.geojson
# Interrupted? Run the same command again: the download continues (MBTiles in edge/data/basemaps/.cache).
# The web map shows the file as layer "Luftbild (offline)" when MAP_ORTHO_PMTILES_URL is set in edge/.env.
set -euo pipefail
cd "$(dirname "$0")"
HERE="$(pwd)"
EDGE="$(cd ../../edge && pwd)"
NAME="${1:-luftbild-home}"
AREA="${2:-5}"
MAXZOOM="${MAXZOOM:-19}"
GDAL_IMAGE="$(sed -n 's/^FROM \(ghcr.io\/osgeo\/gdal:[^ ]*\).*/\1/p' ../../farming-guide/service/Dockerfile)"
PMTILES_IMAGE=protomaps/go-pmtiles@sha256:06574f01f55a78f78f887bc7ebf729a5c093c0d6e17d9876300cfcb0758b59d3  # v1.31.2
OUT="$EDGE/data/basemaps"
mkdir -p "$OUT/.cache"

if [ -f "$AREA" ]; then
  AREA_ARGS=(--region "/work/$(realpath --relative-to="$HERE" "$AREA")")
else
  HOME_POINT="$(sed -n 's/^HOME_POINT=//p' "$EDGE/.env")"
  [ -n "$HOME_POINT" ] || { echo "ERROR: HOME_POINT missing in edge/.env"; exit 1; }
  AREA_ARGS=(--center "$HOME_POINT" --radius-km "$AREA")
fi

echo "== $NAME: DOP20 tiles z10-$MAXZOOM"
docker run --rm --user "$(id -u):$(id -g)" -v "$HERE:/work:ro" -v "$OUT/.cache:/cache" -e PYTHONUNBUFFERED=1 \
  "$GDAL_IMAGE" python3 /work/fetch_orthophoto.py --out "/cache/$NAME.mbtiles" --name "$NAME" \
  --maxzoom "$MAXZOOM" "${AREA_ARGS[@]}"

echo "== converting to PMTiles"
docker run --rm --user "$(id -u):$(id -g)" -v "$OUT:/out" "$PMTILES_IMAGE" \
  convert "/out/.cache/$NAME.mbtiles" "/out/.$NAME.pmtiles.part"
docker run --rm -v "$OUT:/out:ro" "$PMTILES_IMAGE" verify "/out/.$NAME.pmtiles.part"
mv "$OUT/.$NAME.pmtiles.part" "$OUT/$NAME.pmtiles"
rm -f "$OUT/.cache/$NAME.mbtiles"
printf '{"name":"%s","source":"LGL-BW DOP20","license":"dl-de/by-2-0","attribution":"LGL-BW (%s) dl-de/by-2-0","maxzoom":%d,"created":"%s"}\n' \
  "$NAME" "$(date +%Y)" "$MAXZOOM" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUT/$NAME.json"
echo "== done: $OUT/$NAME.pmtiles ($(du -h "$OUT/$NAME.pmtiles" | cut -f1))"
