#!/usr/bin/env bash
# Installs the DJI Thermal SDK (TSDK) for the Photovoltaik service.
# The SDK is proprietary (DJI license) and therefore NOT part of this repository: download it yourself from
# https://www.dji.com/downloads/softwares/dji-thermal-sdk (accept the license), then
#   ./install-tsdk.sh ~/Downloads/dji_thermal_sdk_v1.8_<date>.zip
# The command line tool dji_irp plus its libraries for this machine's architecture are copied to
# edge/runtime/dji-tsdk/bin (gitignored), which docker-compose mounts read-only into the pv container.
set -euo pipefail
ZIP="${1:?usage: $0 <dji_thermal_sdk_*.zip>}"
cd "$(dirname "$0")"
DEST="$(cd ../../edge && pwd)/runtime/dji-tsdk"
case "$(uname -m)" in
  aarch64|arm64) ARCH_RE='aarch64|arm64|armv8' ;;
  x86_64|amd64)  ARCH_RE='x64|x86_64|amd64' ;;
  *) echo "ERROR: unsupported architecture $(uname -m)"; exit 1 ;;
esac
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
unzip -q "$ZIP" -d "$TMP"
# release build of the dji_irp tool for Linux and this architecture
TOOL="$(find "$TMP" -type f -name dji_irp -path '*linux*' | grep -Ei "$ARCH_RE" | grep -i release | head -1 || true)"
[ -n "$TOOL" ] || TOOL="$(find "$TMP" -type f -name dji_irp -path '*linux*' | grep -Ei "$ARCH_RE" | head -1 || true)"
if [ -z "$TOOL" ]; then
  echo "ERROR: no dji_irp for Linux $(uname -m) in $ZIP. Found:"
  find "$TMP" -type f -name 'dji_irp*' | sed "s|$TMP/||"
  exit 1
fi
rm -rf "$DEST/bin"
mkdir -p "$DEST/bin"
cp -a "$(dirname "$TOOL")"/. "$DEST/bin/"
chmod +x "$DEST/bin/dji_irp"
find "$TMP" -maxdepth 3 -iname 'license*' -exec cp {} "$DEST/" \; 2>/dev/null || true
find "$TMP" -maxdepth 3 -iname 'release*note*' -exec cp {} "$DEST/" \; 2>/dev/null || true
echo "installed $(basename "$ZIP") ($(uname -m)) -> $DEST/bin:"
ls "$DEST/bin"
echo "check: docker compose exec pv /opt/dji-tsdk/bin/dji_irp -h"
