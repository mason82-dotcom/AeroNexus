"""Status page of the web UI: services, host (edge/status.sh -> data/status/host.json), compute agents, jobs,
the day's work and warnings. The host file is written by a cron job on the host, so this service needs no access
to the host or the data disks; a missing or old file is itself a warning."""
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

log = logging.getLogger("mapping.status")

HOST_FILE = os.environ.get("HOST_STATUS_FILE", "/host-status/host.json")
HOST_STALE_S = 15 * 60
AGENT_STALE_S = 24 * 3600
BACKUP_WARN_S = 36 * 3600
BACKUP_ERROR_S = 7 * 24 * 3600
DISK_WARN, DISK_ERROR = 0.10, 0.05          # free share
DISK_WARN_BYTES = 20 * 1024 ** 3
ONE_SHOT = ("minio-init", "mapping-db-init")  # exit 0 is their normal end
DISK_LABELS = {"system": "Systemplatte (SD-Karte)", "minio": "Mediendaten (MinIO)", "backup": "Backup-Platte"}


def _gb(n: int) -> str:
    return f"{n / 1024 ** 3:.0f} GB"


def _age_text(seconds: float) -> str:
    if seconds < 3600:
        return f"{int(seconds // 60)} min"
    if seconds < 48 * 3600:
        return f"{seconds / 3600:.0f} h"
    return f"{seconds / 86400:.0f} Tagen"


def parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def build_warnings(host: dict | None, services: list[dict], jobs: dict, agents: list[dict],
                   now: datetime) -> list[dict]:
    """Problems worth a look, worst first: [{level: error|warning|info, key, text, link}]. Pure, tested."""
    out: list[dict] = []

    def add(level, key, text, link=None):
        out.append({"level": level, "key": key, "text": text, "link": link})

    for s in services:
        if not s["ok"]:
            add("error", f"service:{s['name']}", f"Dienst {s['label']} nicht erreichbar ({s['detail']})")

    host_at = parse_time(host.get("at")) if host else None
    if not host_at:
        add("warning", "host:missing", "Kein Host-Status: edge/status.sh --install-cron auf dem Server ausführen")
    elif (now - host_at).total_seconds() > HOST_STALE_S:
        add("warning", "host:stale", f"Host-Status seit {_age_text((now - host_at).total_seconds())} nicht"
            " aktualisiert (Cron von edge/status.sh läuft nicht?)")
    if host:
        system = next((d for d in host.get("disks", []) if d["name"] == "system"), None)
        for d in host.get("disks", []):
            label = DISK_LABELS.get(d["name"], d["name"])
            if not d.get("exists"):
                add("error", f"disk:{d['name']}", f"{label}: {d['path']} fehlt (Platte nicht eingehängt?)")
                continue
            if d["name"] != "system" and system and d.get("device") and d.get("device") == system.get("device"):
                add("error", f"disk:{d['name']}", f"{label} liegt auf der SD-Karte: Platte nicht eingehängt?")
            if not d.get("total"):
                continue
            share = d["free"] / d["total"]
            if share < DISK_ERROR:
                add("error", f"disk:{d['name']}", f"{label} fast voll: nur noch {_gb(d['free'])} frei")
            elif share < DISK_WARN or d["free"] < DISK_WARN_BYTES:
                add("warning", f"disk:{d['name']}", f"{label}: nur noch {_gb(d['free'])} frei")
        backup = host.get("backup") or {}
        last = parse_time(backup.get("last_success"))
        if not last:
            add("error", "backup:never", "Noch kein erfolgreiches Backup (edge/backup.sh)")
        else:
            age = (now - last).total_seconds()
            if age > BACKUP_ERROR_S:
                add("error", "backup:old", f"Letztes Backup vor {_age_text(age)}")
            elif age > BACKUP_WARN_S:
                add("warning", "backup:old", f"Letztes Backup vor {_age_text(age)}")
        if not backup.get("cron"):
            add("warning", "backup:cron", "Automatisches Backup ist deaktiviert (edge/backup.sh --install-cron)")
        for c in host.get("containers", []):
            if c["service"] in ONE_SHOT:
                if c["state"] == "exited" and c.get("exit_code") not in (0, None):
                    add("error", f"container:{c['service']}", f"Container {c['service']} mit Fehler beendet")
                continue
            if c["state"] != "running":
                add("error", f"container:{c['service']}", f"Container {c['service']} läuft nicht ({c['state']})")
            elif c.get("health") == "unhealthy":
                add("error", f"container:{c['service']}", f"Container {c['service']} meldet unhealthy")
        if host.get("temp_c") and host["temp_c"] >= 75:
            add("warning", "host:temp", f"Server-Temperatur {host['temp_c']:.0f} °C")
        if host.get("mem_total") and host.get("mem_available") is not None \
                and host["mem_available"] / host["mem_total"] < 0.10:
            add("warning", "host:mem", f"Arbeitsspeicher knapp: {_gb(host['mem_available'])} frei")

    failed = jobs.get("failed_recent", [])
    if failed:
        add("warning", "jobs:failed", f"{len(failed)} Kartierungsaufträge in den letzten 7 Tagen fehlgeschlagen",
            "mapping")
    waiting = jobs.get("counts", {}).get("QUEUED", 0)
    fresh = [a for a in agents if a["age_s"] < AGENT_STALE_S]
    if waiting and not fresh:
        last = min((a["age_s"] for a in agents), default=None)
        since = f"zuletzt vor {_age_text(last)}" if last is not None else "noch nie verbunden"
        add("info", "agent:offline", f"{waiting} Kartierungsaufträge warten, Rechenknoten offline ({since})",
            "mapping")
    order = {"error": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda w: order[w["level"]])


def _probe_tcp(host: str, port: int, timeout=2.0) -> str | None:
    with socket.create_connection((host, port), timeout=timeout):
        return None


def _probe_http(url: str, timeout=3.0, want_ok=False) -> str | None:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            r.read(200)
    except urllib.error.HTTPError as exc:          # any HTTP answer means the server is up
        if want_ok:
            return f"HTTP {exc.code}"
    return None


def probe_services(settings) -> list[dict]:
    server = os.environ.get("SERVER_HOST", "")
    checks = [
        ("api", "DJI-Backend", lambda: _probe_http("http://api:6789/")),
        ("emqx", "MQTT (EMQX)", lambda: _probe_tcp("emqx", 1883)),
        ("redis", "Redis", lambda: _probe_tcp("redis", 6379)),
        ("minio", "Objektspeicher (MinIO)", lambda: _probe_http("http://minio:9000/minio/health/live", want_ok=True)),
        ("farming", "Farming", lambda: _probe_http("http://farming:6791/health", want_ok=True)),
        ("pv", "Photovoltaik", lambda: _probe_http("http://pv:6792/health", want_ok=True)),
        ("control", "Weboberfläche", lambda: _probe_http("http://control/")),
    ]
    if server:   # MediaMTX runs with host networking
        checks.append(("mediamtx", "Livestream (MediaMTX)", lambda: _probe_tcp(server, 1935)))

    def run(check):
        name, label, fn = check
        t0 = time.monotonic()
        try:
            detail = fn()
        except Exception as exc:                    # refused, timeout, DNS
            detail = type(exc).__name__ if not str(exc) else str(exc)[:80]
        return {"name": name, "label": label, "ok": detail is None, "detail": detail or "",
                "ms": round((time.monotonic() - t0) * 1000)}

    with ThreadPoolExecutor(max_workers=len(checks)) as pool:
        return list(pool.map(run, checks))


def read_host(path: str = HOST_FILE) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def build_router(settings):
    from fastapi import APIRouter, Depends

    from . import db
    from .auth import User, user_dependency
    from .missions import local_tz

    router = APIRouter(prefix="/api/mapping")
    current_user = user_dependency(settings)
    ingest_table = f"`{settings.mysql_database}`.media_ingest"

    @router.get("/status")
    def status(user: User = Depends(current_user)) -> dict:
        now = datetime.now(timezone.utc)
        naive = now.replace(tzinfo=None)
        t0 = time.monotonic()
        services = probe_services(settings)
        with db.transaction(settings) as cur:
            cur.execute("SELECT 1")
            db_ms = round((time.monotonic() - t0) * 1000)
            cur.execute("SELECT status, COUNT(*) AS n FROM mapping_job WHERE workspace_id = %s GROUP BY status",
                        (user.workspace_id,))
            counts = {r["status"]: r["n"] for r in cur.fetchall()}
            cur.execute("SELECT id, name, status, progress, message, agent_id, created_at, updated_at FROM mapping_job"
                        " WHERE workspace_id = %s AND (status IN ('QUEUED', 'CLAIMED', 'RUNNING')"
                        " OR (status = 'FAILED' AND updated_at >= %s)) ORDER BY created_at",
                        (user.workspace_id, naive - timedelta(days=7)))
            rows = cur.fetchall()
            # agent_seen (since migration 0010) plus the job history for agents not seen since then
            cur.execute("SELECT agent_id, last_seen, capabilities FROM agent_seen UNION ALL"
                        " SELECT agent_id, MAX(COALESCE(finished_at, updated_at)), NULL FROM mapping_job"
                        " WHERE agent_id IS NOT NULL AND agent_id NOT IN (SELECT agent_id FROM agent_seen)"
                        " GROUP BY agent_id ORDER BY 2 DESC")
            agents = [{"agent_id": r["agent_id"], "last_seen": r["last_seen"].isoformat(timespec="seconds") + "Z",
                       "age_s": round((naive - r["last_seen"]).total_seconds()),
                       "capabilities": json.loads(r["capabilities"]) if r["capabilities"] else {}}
                      for r in cur.fetchall()]
            tz = local_tz()
            day0 = datetime.now(tz).replace(hour=0, minute=0, second=0, microsecond=0)
            utc0 = day0.astimezone(timezone.utc).replace(tzinfo=None)
            cur.execute("SELECT id, title, purpose, planned_start, status, drone_sn, pilot FROM mission"
                        " WHERE workspace_id = %s AND planned_start >= %s AND planned_start < %s ORDER BY planned_start",
                        (user.workspace_id, utc0, utc0 + timedelta(days=1)))
            missions_today = [{**r, "planned_start": r["planned_start"].isoformat(timespec="seconds") + "Z"}
                              for r in cur.fetchall()]
            cur.execute("SELECT COUNT(*) AS n FROM mission WHERE workspace_id = %s AND status = 'flown'",
                        (user.workspace_id,))
            to_evaluate = cur.fetchone()["n"]
            cur.execute("SELECT id, drone, kind, shots, first_capture FROM flight_set WHERE workspace_id = %s",
                        (user.workspace_id,))
            all_sets = {r["id"]: {**r, "first_capture": r["first_capture"].isoformat(timespec="seconds")}
                        for r in cur.fetchall()}
        jobs = {"counts": counts,
                "active": [{"id": r["id"], "name": r["name"], "status": r["status"], "progress": float(r["progress"]),
                            "message": r["message"], "agent_id": r["agent_id"],
                            "created_at": r["created_at"].isoformat(timespec="seconds") + "Z"}
                           for r in rows if r["status"] != "FAILED"],
                "failed_recent": [{"id": r["id"], "name": r["name"], "message": r["message"],
                                   "updated_at": r["updated_at"].isoformat(timespec="seconds") + "Z"}
                                  for r in rows if r["status"] == "FAILED"]}
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                since_ms = int((now - timedelta(days=1)).timestamp() * 1000)
                cur.execute(f"SELECT COALESCE(NULLIF(i.drone, ''), f.drone, '') AS drone, COUNT(*) AS n,"
                            f" MAX(f.create_time) AS last FROM media_file f LEFT JOIN {ingest_table} i"
                            f" ON i.file_id = f.file_id WHERE f.workspace_id = %s AND f.create_time >= %s"
                            f" GROUP BY 1 ORDER BY n DESC", (user.workspace_id, since_ms))
                uploads = cur.fetchall()
                # "new" flight sets: files uploaded in the last 24 h (flight_set.created_at changes on regrouping)
                cur.execute(f"SELECT i.flight_set_id AS id, COUNT(*) AS n FROM media_file f JOIN {ingest_table} i"
                            f" ON i.file_id = f.file_id WHERE f.workspace_id = %s AND f.create_time >= %s"
                            f" AND i.flight_set_id IS NOT NULL GROUP BY i.flight_set_id", (user.workspace_id, since_ms))
                uploaded_sets = {r["id"]: r["n"] for r in cur.fetchall()}
                cur.execute("SELECT MAX(create_time) AS last FROM media_file WHERE workspace_id = %s",
                            (user.workspace_id,))
                last_upload = cur.fetchone()["last"]
                cur.execute("SELECT device_sn, nickname, device_name FROM manage_device WHERE workspace_id = %s",
                            (user.workspace_id,))
                names = {r["device_sn"]: r["nickname"] or r["device_name"] for r in cur.fetchall()}
        finally:
            conn.close()
        services.insert(0, {"name": "mysql", "label": "Datenbank (MySQL)", "ok": True, "detail": "", "ms": db_ms})
        services.insert(1, {"name": "mapping", "label": "Mapping-Dienst", "ok": True, "detail": "", "ms": 0})
        host = read_host()
        if host:
            at = parse_time(host.get("at"))
            host["age_s"] = round((now - at).total_seconds()) if at else None
        today = {
            "missions": missions_today, "to_evaluate": to_evaluate,
            "uploads_24h": sum(r["n"] for r in uploads),
            "uploads_by_drone": [{"drone": r["drone"], "name": names.get(r["drone"], ""), "files": r["n"]}
                                 for r in uploads],
            "last_upload": datetime.fromtimestamp(last_upload / 1000, timezone.utc).isoformat(timespec="seconds")
            .replace("+00:00", "Z") if last_upload else None,
            "flight_sets_24h": sorted(({**all_sets[i], "drone_name": names.get(all_sets[i]["drone"], ""),
                                        "uploaded": n} for i, n in uploaded_sets.items() if i in all_sets),
                                      key=lambda x: x["first_capture"], reverse=True),
        }
        return {"now": now.isoformat(timespec="seconds").replace("+00:00", "Z"), "services": services, "host": host,
                "agents": agents, "jobs": jobs, "today": today,
                "warnings": build_warnings(host, services, jobs, agents, now)}

    return router
