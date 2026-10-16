"""Automatic preparation of media uploaded by Pilot 2 (runs in the background after every upload).

For each new file in cloud_sample.media_file: category from the DJI file name, shot key (all files of one
shutter release), metadata (EXIF/XMP into media_meta) and thumbnail prepared ahead, and assignment to a
flight set of the drone that took it (aircraft serial from XMP, Pilot 2's drone only as fallback): shots of the same drone at most FLIGHT_GAP apart, independent of the upload order (manual
uploads arrive out of order). Nothing here starts a mapping job or an inspection; the sets only make them
one click away. Thermal temperature matrices are pre-measured by the pv service itself.
"""
import json
import logging
import re
import threading
import uuid
from collections import Counter
from datetime import datetime, timedelta, timezone

from . import mediameta, thumbs

log = logging.getLogger("mapping.ingest")

FLIGHT_GAP = timedelta(minutes=15)
# The files of one shutter release may carry different seconds in their names (Pilot 2 often stamps the
# visible or RGB frame 1 s later, seen 2026-10-01: ..._223817_0001_F and ..._223818_0001_D).
SHOT_TOLERANCE = timedelta(seconds=2)
BATCH = 20
INTERVAL_SECONDS = 60
LOCK_NAME = "aeronexus_flight_sets"

_SHOT_RE = re.compile(r"^(DJI_(\d{14})_\d{4})_")
_VIDEO_EXT = (".mp4", ".mov")


# ------------------------------------------------------------------ pure core

def classify(file_name: str, sub_file_type: int | None = None) -> tuple[str, str | None]:
    """(category, multispectral band) from a DJI media file name."""
    name = file_name.upper()
    if name.endswith(tuple(e.upper() for e in _VIDEO_EXT)):
        return "video", None
    if sub_file_type == 1:
        return "panorama", None
    stem = name.rsplit(".", 1)[0]
    ms = re.search(r"_MS_(G|R|RE|NIR)$", stem)
    if ms:
        return "multispectral", ms.group(1)
    suffix = stem.rsplit("_", 1)[-1] if "_" in stem else ""
    if suffix == "F":                       # Mavic 3M composite next to the bands
        return "multispectral", "F"
    if suffix == "T":
        return "thermal", None              # R-JPEG (M3T / M4T)
    if suffix == "V":
        return "visible", None              # visible frame of a thermal shot
    if suffix in ("W", "Z", "D"):
        return "rgb", None                  # wide / zoom / Mavic 3M RGB
    return "other", None


# drones whose _V photo is the visible frame of a thermal shot; on any other drone _V is a plain RGB photo
# (2026-10-16: 203 Mavic 3E photos were filed as "visible")
THERMAL_MODELS = frozenset({"M3T", "M3TD", "M4T", "M4TD", "M30T"})


def refine_category(category: str, meta: dict | None) -> str:
    """Category from the file name, corrected by the camera model in EXIF/XMP where it tells more."""
    model = (meta or {}).get("model")
    if category == "visible" and model and model not in THERMAL_MODELS:
        return "rgb"
    return category


def shot_info(file_name: str) -> tuple[str | None, datetime | None]:
    """(shot key, camera time) from DJI_<yyyymmddhhmmss>_<nnnn>_..."""
    m = _SHOT_RE.match(file_name)
    if not m:
        return None, None
    try:
        return m.group(1), datetime.strptime(m.group(2), "%Y%m%d%H%M%S")
    except ValueError:
        return m.group(1), None


def shot_index(shot_key: str | None) -> str | None:
    return shot_key.rsplit("_", 1)[-1] if shot_key else None


def same_shot(key_a: str | None, time_a: datetime | None, key_b: str | None, time_b: datetime | None) -> bool:
    """Same image number and at most SHOT_TOLERANCE apart."""
    if not key_a or not key_b or time_a is None or time_b is None:
        return False
    return shot_index(key_a) == shot_index(key_b) and abs(time_a - time_b) <= SHOT_TOLERANCE


def find_set(sets: list[dict], captured: datetime) -> dict | None:
    """The set of the same drone this shot belongs to (within FLIGHT_GAP of its time range)."""
    best = None
    for s in sets:
        if s["first_capture"] - FLIGHT_GAP <= captured <= s["last_capture"] + FLIGHT_GAP:
            if best is None or abs(captured - s["last_capture"]) < abs(captured - best["last_capture"]):
                best = s
    return best


def set_kind(categories: Counter) -> str:
    present = {c for c, n in categories.items() if n}
    if "multispectral" in present:
        return "multispectral"
    if "thermal" in present:
        return "thermal"
    if present <= {"rgb", "visible", "panorama"} and present:
        return "rgb"                        # visible frames without thermal ones are ordinary photos
    if present == {"video"}:
        return "video"
    return "mixed"


def effective_drone(meta: dict | None, pilot_drone: str | None) -> str:
    """Drone of a file: serial written by the aircraft (XMP) if present, else what Pilot 2 reported."""
    return (meta or {}).get("drone_serial") or pilot_drone or ""


# ------------------------------------------------------------- MinIO + MySQL

def read_meta(settings, storage, row: dict) -> dict:
    """EXIF/XMP from the first 256 KB (range read); whole file only if the head is not enough."""
    stat = storage.internal.stat_object(settings.media_bucket, row["object_key"])
    resp = storage.internal.get_object(settings.media_bucket, row["object_key"], offset=0,
                                       length=min(mediameta.HEAD_BYTES, stat.size))
    try:
        head = resp.read()
    finally:
        resp.close()
        resp.release_conn()
    try:
        meta = mediameta.extract(head, stat.size, row["file_name"])
    except Exception:
        meta = None
    if meta is None or meta["width"] is None or meta["captured"] is None:
        resp = storage.internal.get_object(settings.media_bucket, row["object_key"])
        try:
            meta = mediameta.extract(resp.read(), stat.size, row["file_name"])
        finally:
            resp.close()
            resp.release_conn()
    return meta


def _load_original(settings, storage, key: str) -> bytes:
    resp = storage.internal.get_object(settings.media_bucket, key)
    try:
        return resp.read()
    finally:
        resp.close()
        resp.release_conn()


def _pending(cur, settings, limit: int) -> list[dict]:
    cur.execute(
        f"SELECT m.file_id, m.workspace_id, m.file_name, m.object_key, m.drone, m.sub_file_type,"
        f" m.create_time FROM `{settings.media_database}`.media_file m"
        f" LEFT JOIN `{settings.mysql_database}`.media_ingest i ON i.file_id = m.file_id"
        f" WHERE i.file_id IS NULL AND m.object_key <> '' ORDER BY m.id LIMIT %s", (limit,))
    return cur.fetchall()


def _prepare_file(settings, storage, row: dict) -> dict:
    """Category, metadata and thumbnail of one file (no database writes except media_meta)."""
    category, band = classify(row["file_name"], row.get("sub_file_type"))
    shot_key, captured = shot_info(row["file_name"])
    out = {"category": category, "band": band, "shot_key": shot_key, "captured_at": captured,
           "meta": None, "meta_ok": False, "thumb_ok": False, "note": None}
    if category != "video":
        try:
            out["meta"] = read_meta(settings, storage, row)
            out["meta_ok"] = True
            out["category"] = refine_category(category, out["meta"])
            if out["captured_at"] is None and out["meta"].get("captured"):
                out["captured_at"] = datetime.fromisoformat(out["meta"]["captured"]).replace(tzinfo=None)
        except Exception as exc:
            out["note"] = f"metadata: {type(exc).__name__}"
    if thumbs.supported(row["file_name"]):
        try:
            thumbs.get_or_create(settings.cache_dir, row["file_id"],
                                 lambda: _load_original(settings, storage, row["object_key"]))
            out["thumb_ok"] = True
        except Exception as exc:
            out["note"] = (out["note"] + "; " if out["note"] else "") + f"thumbnail: {type(exc).__name__}"
    if out["captured_at"] is None and row.get("create_time"):
        out["captured_at"] = datetime.fromtimestamp(row["create_time"] / 1000, tz=timezone.utc).replace(tzinfo=None)
    return out


def _refresh_set(cur, set_id: str) -> None:
    cur.execute("SELECT category, COUNT(*) AS n, COUNT(DISTINCT shot_key) AS shots, MIN(captured_at) AS first,"
                " MAX(captured_at) AS last FROM media_ingest WHERE flight_set_id = %s GROUP BY category", (set_id,))
    rows = cur.fetchall()
    counts = Counter({r["category"]: r["n"] for r in rows})
    cur.execute("SELECT COUNT(DISTINCT COALESCE(shot_key, file_id)) AS shots FROM media_ingest"
                " WHERE flight_set_id = %s", (set_id,))
    shots = cur.fetchone()["shots"]
    cur.execute("UPDATE flight_set SET kind = %s, shots = %s, files = %s, first_capture = %s, last_capture = %s,"
                " updated_at = UTC_TIMESTAMP(3) WHERE id = %s",
                (set_kind(counts), shots, sum(counts.values()), min(r["first"] for r in rows),
                 max(r["last"] for r in rows), set_id))


def _resolve_shot(cur, row: dict, shot_key: str | None, captured: datetime | None) -> tuple[str | None, str | None]:
    """(shot key, flight set) of an already prepared file of the same shot, else (own key, None)."""
    if not shot_key or captured is None:
        return shot_key, None
    cur.execute("SELECT shot_key, captured_at, flight_set_id FROM media_ingest WHERE workspace_id = %s"
                " AND shot_key LIKE %s AND captured_at BETWEEN %s AND %s",
                (row["workspace_id"], f"DJI\\_%\\_{shot_index(shot_key)}", captured - SHOT_TOLERANCE,
                 captured + SHOT_TOLERANCE))
    for other in cur.fetchall():
        if same_shot(shot_key, captured, other["shot_key"], other["captured_at"]):
            return other["shot_key"], other["flight_set_id"]
    return shot_key, None


def _assign_set(cur, row: dict, captured: datetime) -> str:
    # Some uploads carry no drone serial (seen on multispectral TIFs and videos): match any drone then.
    drone = row["drone"] or ""
    cur.execute("SELECT id, drone, first_capture, last_capture FROM flight_set WHERE workspace_id = %s"
                " AND (drone = %s OR %s = '') AND last_capture >= %s AND first_capture <= %s",
                (row["workspace_id"], drone, drone, captured - FLIGHT_GAP, captured + FLIGHT_GAP))
    found = find_set(cur.fetchall(), captured)
    if found:
        return found["id"]
    set_id = str(uuid.uuid4())
    cur.execute("INSERT INTO flight_set (id, workspace_id, drone, kind, first_capture, last_capture)"
                " VALUES (%s, %s, %s, 'mixed', %s, %s)",
                (set_id, row["workspace_id"], row["drone"] or "", captured, captured))
    return set_id


def prepare_pending(settings, storage, db, limit: int = BATCH) -> int:
    """Prepare up to `limit` new media files. Returns the number prepared."""
    conn = db.connect(settings, settings.mysql_database)
    done = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0) AS got", (LOCK_NAME,))
            if not cur.fetchone()["got"]:
                return 0
            try:
                pending = _pending(cur, settings, limit)
                conn.commit()
                for row in pending:
                    prep = _prepare_file(settings, storage, row)
                    if prep["meta"] is not None:
                        cur.execute("INSERT INTO media_meta (file_id, workspace_id, meta) VALUES (%s, %s, %s)"
                                    " ON DUPLICATE KEY UPDATE meta = VALUES(meta), extracted_at = UTC_TIMESTAMP(3)",
                                    (row["file_id"], row["workspace_id"], json.dumps(prep["meta"])))
                    row = {**row, "drone": effective_drone(prep["meta"], row["drone"])}
                    prep["shot_key"], set_id = _resolve_shot(cur, row, prep["shot_key"], prep["captured_at"])
                    if set_id is None and prep["captured_at"]:
                        set_id = _assign_set(cur, row, prep["captured_at"])
                    cur.execute(
                        "INSERT INTO media_ingest (file_id, workspace_id, category, band, shot_key, captured_at,"
                        " drone, flight_set_id, meta_ok, thumb_ok, note) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                        (row["file_id"], row["workspace_id"], prep["category"], prep["band"], prep["shot_key"],
                         prep["captured_at"], row["drone"] or "", set_id, prep["meta_ok"], prep["thumb_ok"],
                         prep["note"]))
                    if set_id:
                        _refresh_set(cur, set_id)
                    conn.commit()
                    done += 1
                    log.info("ingest: %s -> %s%s, set %s", row["file_name"], prep["category"],
                             f"/{prep['band']}" if prep["band"] else "", (set_id or "-")[:8])
            finally:
                cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
                conn.commit()
    finally:
        conn.close()
    return done


def _stale(cur, settings, limit: int) -> list[dict]:
    """Prepared files whose stored metadata comes from an older extractor version, or whose aircraft serial
    differs from their drone (metadata re-read by the media list API before this pass saw the file)."""
    cur.execute(
        f"SELECT i.file_id, i.workspace_id, i.drone, i.flight_set_id, i.captured_at, m.file_name, m.object_key,"
        f" m.drone AS pilot_drone FROM media_ingest i"
        f" JOIN `{settings.media_database}`.media_file m ON m.file_id = i.file_id"
        f" JOIN media_meta mm ON mm.file_id = i.file_id"
        f" WHERE i.meta_ok = 1 AND (COALESCE(JSON_EXTRACT(mm.meta, '$.v'), 0) < %s"
        f" OR JSON_UNQUOTE(JSON_EXTRACT(mm.meta, '$.drone_serial')) <> i.drone) LIMIT %s",
        (mediameta.VERSION, limit))
    return cur.fetchall()


def _move_to_drone(cur, row: dict, drone: str) -> str | None:
    """Put an already prepared file into the flight set of `drone`; empty sets left behind are removed."""
    old = row["flight_set_id"]
    cur.execute("UPDATE media_ingest SET drone = %s, flight_set_id = NULL WHERE file_id = %s", (drone, row["file_id"]))
    new = _assign_set(cur, {**row, "drone": drone}, row["captured_at"]) if row["captured_at"] else None
    cur.execute("UPDATE media_ingest SET flight_set_id = %s WHERE file_id = %s", (new, row["file_id"]))
    if new:
        _refresh_set(cur, new)
    if old and old != new:
        cur.execute("SELECT COUNT(*) AS n FROM media_ingest WHERE flight_set_id = %s", (old,))
        if cur.fetchone()["n"]:
            _refresh_set(cur, old)
        else:
            cur.execute("DELETE FROM flight_set WHERE id = %s", (old,))
    return new


def refresh_stale(settings, storage, db, limit: int = BATCH) -> int:
    """Re-extract up to `limit` outdated metadata rows; a file whose aircraft serial names another drone than
    its flight set moves to that drone's set. Returns the number refreshed."""
    conn = db.connect(settings, settings.mysql_database)
    done = 0
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT GET_LOCK(%s, 0) AS got", (LOCK_NAME,))
            if not cur.fetchone()["got"]:
                return 0
            try:
                for row in _stale(cur, settings, limit):
                    try:
                        meta = read_meta(settings, storage, row)
                    except Exception as exc:
                        log.warning("ingest: metadata of %s not readable: %s", row["file_name"], exc)
                        meta = {"v": mediameta.VERSION, "error": "not readable"}
                    cur.execute("UPDATE media_meta SET meta = %s, extracted_at = UTC_TIMESTAMP(3) WHERE file_id = %s",
                                (json.dumps(meta), row["file_id"]))
                    drone = effective_drone(meta, row["pilot_drone"])
                    if drone != (row["drone"] or ""):
                        new = _move_to_drone(cur, row, drone)
                        log.info("ingest: %s drone %s -> %s (aircraft serial), set %s", row["file_name"],
                                 row["drone"] or "-", drone, (new or "-")[:8])
                    conn.commit()
                    done += 1
            finally:
                cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
                conn.commit()
    finally:
        conn.close()
    return done


def start(settings, storage, db, stop: threading.Event) -> threading.Thread:
    def run():
        while not stop.is_set():
            try:
                while prepare_pending(settings, storage, db) == BATCH and not stop.is_set():
                    pass
                # older metadata (extractor version bump): re-read in the background, BATCH per pass
                while refresh_stale(settings, storage, db) == BATCH and not stop.is_set():
                    pass
            except Exception as exc:
                log.warning("ingest round failed: %s", exc)
            stop.wait(INTERVAL_SECONDS)
    thread = threading.Thread(target=run, name="media-ingest", daemon=True)
    thread.start()
    return thread


# ------------------------------------------------------------------ web API

def build_router(settings):
    from fastapi import APIRouter, Depends, HTTPException

    from . import db
    from .auth import User, user_dependency

    router = APIRouter(prefix="/api/mapping")
    current_user = user_dependency(settings)

    def iso(value):
        return value.isoformat(timespec="seconds") if value else None

    @router.get("/flight-sets")
    def list_sets(user: User = Depends(current_user)) -> list[dict]:
        """Flight sets, newest first, with file counts per category."""
        with db.transaction(settings) as cur:
            cur.execute("SELECT * FROM flight_set WHERE workspace_id = %s ORDER BY last_capture DESC LIMIT 200",
                        (user.workspace_id,))
            sets = cur.fetchall()
            cur.execute("SELECT i.flight_set_id, i.category, COUNT(*) AS n FROM media_ingest i JOIN flight_set s"
                        " ON s.id = i.flight_set_id WHERE s.workspace_id = %s GROUP BY i.flight_set_id, i.category",
                        (user.workspace_id,))
            counts = {}
            for r in cur.fetchall():
                counts.setdefault(r["flight_set_id"], {})[r["category"]] = r["n"]
        return [{"id": s["id"], "drone": s["drone"], "kind": s["kind"], "first_capture": iso(s["first_capture"]),
                 "last_capture": iso(s["last_capture"]), "shots": s["shots"], "files": s["files"],
                 "by_category": counts.get(s["id"], {})} for s in sets]

    @router.get("/flight-sets/{set_id}")
    def get_set(set_id: str, user: User = Depends(current_user)) -> dict:
        """file_ids per category (ready for a PV inspection or a mapping job) and the shots."""
        with db.transaction(settings) as cur:
            cur.execute("SELECT * FROM flight_set WHERE id = %s AND workspace_id = %s", (set_id, user.workspace_id))
            s = cur.fetchone()
            if not s:
                raise HTTPException(status_code=404, detail="Flug-Set nicht gefunden")
            cur.execute("SELECT file_id, category, band, shot_key, captured_at, meta_ok, thumb_ok FROM media_ingest"
                        " WHERE flight_set_id = %s ORDER BY captured_at, shot_key, category, band", (set_id,))
            files = cur.fetchall()
        by_category: dict[str, list[str]] = {}
        shots: dict[str, dict] = {}
        for f in files:
            by_category.setdefault(f["category"], []).append(f["file_id"])
            key = f["shot_key"] or f["file_id"]
            shot = shots.setdefault(key, {"shot_key": f["shot_key"], "captured_at": iso(f["captured_at"]),
                                          "files": {}})
            shot["files"][f"{f['category']}/{f['band']}" if f["band"] else f["category"]] = f["file_id"]
        return {"id": s["id"], "drone": s["drone"], "kind": s["kind"], "first_capture": iso(s["first_capture"]),
                "last_capture": iso(s["last_capture"]), "file_ids": by_category, "shots": list(shots.values()),
                "prepared": {"meta": sum(f["meta_ok"] for f in files), "thumbnail": sum(f["thumb_ok"] for f in files),
                             "files": len(files)}}

    return router
