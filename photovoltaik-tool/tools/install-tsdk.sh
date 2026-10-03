#!/usr/bin/env bash
# Installs the DJI Thermal SDK (TSDK) for the Photovoltaik service (tested with v1.8 20250829).
# The SDK is proprietary (DJI license) and therefore NOT part of this repository: download it yourself from
# https://www.dji.com/downloads/softwares/dji-thermal-sdk (accept the license), then
#   ./install-tsdk.sh ~/Downloads/dji_thermal_sdk_v1.8_<date>.zip
# DJI ships Linux builds for x64 only: dji_irp plus its libraries (release_x64) are copied to
# edge/runtime/dji-tsdk/bin (gitignored) and mounted read-only into the tsdk container, which runs them
# natively on x64 and with Box64 on ARM64 (Pi 5).
set -euo pipefail
ZIP="${1:?usage: $0 <dji_thermal_sdk_*.zip>}"
cd "$(dirname "$0")"
DEST="$(cd ../../edge && pwd)/runtime/dji-tsdk"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
unzip -q "$ZIP" -d "$TMP"
# release build of the dji_irp tool for Linux x64
TOOL="$(find "$TMP" -type f -name dji_irp -path '*utility/bin/linux/release_x64/*' | head -1 || true)"
if [ -z "$TOOL" ]; then
  echo "ERROR: no utility/bin/linux/release_x64/dji_irp in $ZIP. Found:"
  find "$TMP" -type f -name 'dji_irp*' | sed "s|$TMP/||"
  exit 1
fi
rm -rf "${DEST:?}/bin"
mkdir -p "$DEST/bin"
cp -a "$(dirname "$TOOL")"/. "$DEST/bin/"
chmod +x "$DEST/bin/dji_irp"
find "$TMP" -maxdepth 3 -iname 'license*' -exec cp {} "$DEST/" \; 2>/dev/null || true
find "$TMP" -maxdepth 3 -iname 'release*note*' -exec cp {} "$DEST/" \; 2>/dev/null || true
echo "installed $(basename "$ZIP") (linux x64) -> $DEST/bin:"
ls "$DEST/bin"
echo "next: cd edge && docker compose restart tsdk pv   (check: curl -s localhost:6792/api/pv/status with login)"
