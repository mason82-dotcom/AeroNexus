#!/usr/bin/env bash
# Host status for the web status page: disks, backup, containers, temperature -> data/status/host.json.
# The mapping service reads the file (read-only mount) instead of getting host or data disk access.
#   ./status.sh                  write the file once
#   ./status.sh --install-cron   every 5 minutes via cron (keeps other entries)
set -euo pipefail
cd "$(dirname "$0")"
EDGE="$(pwd)"

if [ "${1:-}" = "--install-cron" ]; then
  line="*/5 * * * * $EDGE/status.sh >/dev/null 2>&1"
  tmp="$(mktemp)"
  { crontab -l 2>/dev/null || true; } | { grep -vF "$EDGE/status.sh" || true; } > "$tmp"
  echo "$line" >> "$tmp"
  crontab "$tmp" && rm -f "$tmp"
  echo "cron installed: $line"; exit 0
fi

[ -f .env ] || { echo "ERROR: .env missing"; exit 1; }
set -a; source .env; set +a
mkdir -p data/status

crontab="$(crontab -l 2>/dev/null || true)"
containers="$(docker compose ps -a --format json 2>/dev/null || true)"

EDGE="$EDGE" CRONTAB="$crontab" CONTAINERS="$containers" \
MINIO_DATA_DIR="${MINIO_DATA_DIR:-./data/minio}" BACKUP_DIR="${BACKUP_DIR:-/mnt/data/aeronexus-backup}" \
python3 - <<'EOF'
import json, os, socket, subprocess, time
from datetime import datetime, timezone

edge = os.environ["EDGE"]


def disk(name, path):
    path = os.path.normpath(os.path.join(edge, path))
    out = {"name": name, "path": path, "exists": os.path.isdir(path)}
    if not out["exists"]:
        return out
    st = os.statvfs(path)
    total, free = st.f_blocks * st.f_frsize, st.f_bavail * st.f_frsize
    out.update(total=total, free=free, used=total - st.f_bfree * st.f_frsize)
    try:   # mount point and device: an unmounted data disk shows up as the SD card
        src, target = subprocess.run(["findmnt", "-n", "-o", "SOURCE,TARGET", "--target", path], capture_output=True,
                                     text=True, timeout=5).stdout.split()[:2]
        out.update(device=src, mount=target)
    except Exception:
        pass
    return out


def read(path, limit=2000):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read(limit)
    except OSError:
        return None


backup_dir = os.environ["BACKUP_DIR"]
last = read(os.path.join(backup_dir, "last-success"))
dumps = []
try:
    dumps = sorted(n for n in os.listdir(os.path.join(backup_dir, "mysql")) if n.endswith(".sql.gz"))
except OSError:
    pass

containers = []
for line in os.environ.get("CONTAINERS", "").splitlines():   # compose v2: one JSON object per line (or one array)
    line = line.strip()
    if not line:
        continue
    try:
        items = json.loads(line)
    except ValueError:
        continue
    for c in items if isinstance(items, list) else [items]:
        containers.append({"service": c.get("Service"), "state": c.get("State"), "health": c.get("Health") or "",
                           "exit_code": c.get("ExitCode"), "status": c.get("Status")})

temp = read("/sys/class/thermal/thermal_zone0/temp")
mem = {}
for line in (read("/proc/meminfo", 4000) or "").splitlines():
    k, _, v = line.partition(":")
    if k in ("MemTotal", "MemAvailable"):
        mem[k] = int(v.split()[0]) * 1024

status = {
    "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
    "hostname": socket.gethostname(),
    "uptime_s": int(float((read("/proc/uptime") or "0").split()[0])),
    "load": list(os.getloadavg()),
    "cpu_count": os.cpu_count(),
    "temp_c": round(int(temp) / 1000, 1) if temp and temp.strip().isdigit() else None,
    "mem_total": mem.get("MemTotal"), "mem_available": mem.get("MemAvailable"),
    "disks": [disk("system", "."), disk("minio", os.environ["MINIO_DATA_DIR"]), disk("backup", backup_dir)],
    "backup": {
        "dir": backup_dir,
        "last_success": last.splitlines()[0] if last else None,
        "summary": last.splitlines()[1:] if last else [],
        "dumps": len(dumps), "latest_dump": dumps[-1] if dumps else None,
        # active entry only: disabling by commenting the line out is common
        "cron": any("backup.sh" in line and not line.lstrip().startswith("#")
                    for line in os.environ.get("CRONTAB", "").splitlines()),
    },
    "containers": containers,
}
target = os.path.join(edge, "data/status/host.json")
with open(target + ".tmp", "w") as f:
    json.dump(status, f, indent=1)
os.chmod(target + ".tmp", 0o644)
os.replace(target + ".tmp", target)
EOF
