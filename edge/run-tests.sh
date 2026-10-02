#!/usr/bin/env bash
# Unit tests of the own AeroNexus code, each suite in the image it runs in production
# (same commands locally on the Pi and in GitHub Actions, .github/workflows/ci.yml).
#   ./run-tests.sh [mapping|farming|pv|agent ...]  default: all suites
# Extra checks with real Pilot 2 files (never commit them, they contain the flight site):
#   AERONEXUS_REAL_RJPEG_DIR=/mnt/hc4backup/aeronexus/minio/dji-cloud/wayline ./run-tests.sh pv
#   AERONEXUS_REAL_KMZ_DIR=/mnt/hc4backup/aeronexus/minio/dji-cloud/wayline ./run-tests.sh mapping
set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"
# GDAL base image of farming service and compute agent, pinned by digest in their Dockerfiles
GDAL_IMAGE="$(sed -n 's/^FROM \(ghcr.io\/osgeo\/gdal:[^ ]*\).*/\1/p' farming-guide/service/Dockerfile)"
for f in compute-agent/agent/Dockerfile photovoltaik-tool/service/Dockerfile; do
  grep -qF "FROM $GDAL_IMAGE" "$f" || { echo "ERROR: GDAL image of $f differs from farming"; exit 1; }
done

suites=("$@")
[ ${#suites[@]} -gt 0 ] || suites=(mapping farming pv agent)
rc=0
for suite in "${suites[@]}"; do
  echo "== $suite"
  case "$suite" in
    mapping)
      image="$(docker build -q mapping-tool/service)"
      extra=()
      if [ -n "${AERONEXUS_REAL_KMZ_DIR:-}" ]; then
        extra=(-v "$AERONEXUS_REAL_KMZ_DIR:/kmz:ro" -e AERONEXUS_REAL_KMZ_DIR=/kmz)
      fi
      docker run --rm "${extra[@]}" -v "$ROOT/mapping-tool/service:/srv/src:ro" -w /srv/src \
        -e PYTHONDONTWRITEBYTECODE=1 "$image" python -m unittest discover -s tests -t . || rc=1 ;;
    farming)
      docker run --rm -v "$ROOT/farming-guide/service:/app:ro" -w /app -e PYTHONDONTWRITEBYTECODE=1 \
        "$GDAL_IMAGE" python3 -m unittest discover -s tests -t . || rc=1 ;;
    pv)
      extra=()
      if [ -n "${AERONEXUS_REAL_RJPEG_DIR:-}" ]; then
        extra=(-v "$AERONEXUS_REAL_RJPEG_DIR:/rjpeg:ro" -e AERONEXUS_REAL_RJPEG_DIR=/rjpeg)
      fi
      docker run --rm "${extra[@]}" -v "$ROOT/photovoltaik-tool/service:/app:ro" -w /app -e PYTHONDONTWRITEBYTECODE=1 \
        "$GDAL_IMAGE" python3 -m unittest discover -s tests -t . || rc=1 ;;
    agent)
      # needs the repo root: index definitions live in farming-guide/indices.json
      docker run --rm -v "$ROOT:/repo:ro" -w /repo/compute-agent/agent -e PYTHONDONTWRITEBYTECODE=1 \
        "$GDAL_IMAGE" python3 -m unittest discover -s tests -t . 2>&1 \
        | grep -v "Unable to save auxiliary information" ; [ "${PIPESTATUS[0]}" -eq 0 ] || rc=1 ;;
    *) echo "unknown suite $suite"; rc=1 ;;
  esac
done
[ $rc -eq 0 ] && echo "== all tests passed" || echo "== TESTS FAILED"
exit $rc
