#!/usr/bin/env bash
# Offline basemap for the web map: cuts a region out of the daily Protomaps OpenStreetMap build
# (vector tiles, one PMTiles file) and stores it in edge/data/basemaps/<region>.pmtiles.
# Only the tiles of the region are downloaded (HTTP range requests), not the 138 GB planet.
# The control nginx serves the file at /basemaps/<region>.pmtiles (edge/docker-compose.yml).
#   ./fetch-basemap.sh [region] [build]     defaults: baden-wuerttemberg, newest build
# Re-run to update (e.g. monthly); the old file is replaced atomically.
# Data: (c) OpenStreetMap contributors, ODbL. Basemap schema: Protomaps (https://protomaps.com).
set -euo pipefail
cd "$(dirname "$0")"
REGION="${1:-baden-wuerttemberg}"
BUILD="${2:-}"
MAXZOOM=15                     # Protomaps basemap max zoom; the browser overzooms beyond
PMTILES_IMAGE=protomaps/go-pmtiles@sha256:06574f01f55a78f78f887bc7ebf729a5c093c0d6e17d9876300cfcb0758b59d3  # v1.31.2
OUT_DIR="$(cd ../../edge && pwd)/data/basemaps"

[ -f "regions/$REGION.geojson" ] || { echo "ERROR: regions/$REGION.geojson missing"; exit 1; }
if [ -z "$BUILD" ]; then
  BUILD="$(curl -fsS https://build-metadata.protomaps.dev/builds.json \
           | python3 -c 'import json,sys; print(json.load(sys.stdin)[-1]["key"].removesuffix(".pmtiles"))')"
fi
mkdir -p "$OUT_DIR"
echo "== $REGION from Protomaps build $BUILD (maxzoom $MAXZOOM) -> $OUT_DIR/$REGION.pmtiles"
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/regions:/regions:ro" -v "$OUT_DIR:/out" "$PMTILES_IMAGE" \
  extract "https://build.protomaps.com/$BUILD.pmtiles" "/out/.$REGION.pmtiles.part" \
  --region="/regions/$REGION.geojson" --maxzoom="$MAXZOOM" --download-threads=4
docker run --rm -v "$OUT_DIR:/out:ro" "$PMTILES_IMAGE" verify "/out/.$REGION.pmtiles.part"
mv "$OUT_DIR/.$REGION.pmtiles.part" "$OUT_DIR/$REGION.pmtiles"
printf '{"region":"%s","build":"%s","maxzoom":%d,"created":"%s"}\n' "$REGION" "$BUILD" "$MAXZOOM" \
  "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$OUT_DIR/$REGION.json"
echo "== done: $(du -h "$OUT_DIR/$REGION.pmtiles" | cut -f1)"
