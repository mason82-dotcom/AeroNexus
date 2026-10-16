"""Media list for the web UI with filters (category, drone, flight set, capture date, name).

The DJI backend's media list knows only what Pilot 2 reported; this one joins the ingest results (category from
name and camera model, drone from the aircraft serial, flight set, capture time). Downloads stay with the
DJI backend (file_id).
"""
import json
import math
import re
import statistics
from datetime import date, datetime, timedelta, timezone

CATEGORIES = ("thermal", "visible", "rgb", "multispectral", "video", "panorama", "other")
_SERIAL_RE = re.compile(r"^[0-9A-Z]{4,32}$")
_ID_RE = re.compile(r"^[0-9a-f-]{4,36}$")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") if value else None


def build_filters(category: str | None, drone: str | None, flight_set: str | None, date_from: date | None,
                  date_to: date | None, q: str | None) -> tuple[str, list]:
    """SQL WHERE fragment (aliases f = media_file, i = media_ingest) and its parameters. Pure, tested."""
    where, params = [], []
    cats = [c for c in (category or "").split(",") if c]
    if cats:
        if any(c not in CATEGORIES for c in cats):
            raise ValueError("unknown category")
        where.append("i.category IN (" + ",".join(["%s"] * len(cats)) + ")")
        params += cats
    if drone:
        if drone == "-":
            where.append("COALESCE(i.drone, f.drone, '') = ''")
        elif _SERIAL_RE.match(drone):
            where.append("COALESCE(NULLIF(i.drone, ''), f.drone) = %s")
            params.append(drone)
        else:
            raise ValueError("invalid drone serial")
    if flight_set:
        if not _ID_RE.match(flight_set):
            raise ValueError("invalid flight set id")
        where.append("i.flight_set_id LIKE %s")
        params.append(flight_set + "%")
    if date_from:
        where.append("i.captured_at >= %s")
        params.append(datetime.combine(date_from, datetime.min.time()))
    if date_to:
        where.append("i.captured_at < %s")
        params.append(datetime.combine(date_to + timedelta(days=1), datetime.min.time()))
    if q:
        where.append("f.file_name LIKE %s")
        params.append("%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%")
    return (" AND " + " AND ".join(where)) if where else "", params


def classify_set(rel_alt_median: float | None, nadir_share: float | None, shots: int) -> str:
    """mapping (nadir, in the air, enough shots), oblique (in the air, camera tilted: 3D / inspection),
    ground (test on the ground), flight (anything else in the air), unknown (no metadata). Pure, tested."""
    if rel_alt_median is None:
        return "unknown"
    if abs(rel_alt_median) < 3:
        return "ground"
    if nadir_share is not None and nadir_share >= 0.5 and shots >= 20:
        return "mapping"
    if shots >= 20:
        return "oblique"
    return "flight"


def set_summary(metas: list[dict]) -> dict:
    """Statistics of a flight set from the photo metadata (EXIF/XMP). Pure, tested."""
    rel = [m["rel_alt"] for m in metas if m.get("rel_alt") is not None]
    pitch = [m["gimbal_pitch"] for m in metas if m.get("gimbal_pitch") is not None]
    gnss = [m.get("gps_status") for m in metas if m.get("gps_status")]
    pos = [(m["lat"], m["lon"]) for m in metas if m.get("lat") is not None and m.get("lon") is not None]
    out = {"rel_alt_median": round(statistics.median(rel), 1) if rel else None,
           "rel_alt_max": round(max(rel), 1) if rel else None,
           "nadir_share": round(sum(1 for p in pitch if p <= -80) / len(pitch), 2) if pitch else None,
           "rtk_share": round(sum(1 for g in gnss if g == "RTK") / len(gnss), 2) if gnss else None,
           "center": None, "bbox": None}
    if pos:
        lat = statistics.median(p[0] for p in pos)
        lon = statistics.median(p[1] for p in pos)
        # drop single wrong fixes (seen: one photo 1558 km off) before the extent
        near = [p for p in pos if math.hypot((p[0] - lat) * 111.32, (p[1] - lon) * 111.32 * math.cos(math.radians(lat))) < 20]
        out["center"] = [round(lat, 6), round(lon, 6)]
        out["bbox"] = [round(min(p[0] for p in near), 6), round(min(p[1] for p in near), 6),
                       round(max(p[0] for p in near), 6), round(max(p[1] for p in near), 6)]
    return out


def build_router(settings, storage=None):
    from fastapi import APIRouter, Depends, HTTPException, Query

    from . import db
    from .auth import User, user_dependency

    router = APIRouter(prefix="/api/mapping")
    current_user = user_dependency(settings)
    ingest_table = f"`{settings.mysql_database}`.media_ingest"

    @router.get("/media/browse")
    def browse(user: User = Depends(current_user), page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=200), category: str | None = Query(None, max_length=200),
               drone: str | None = Query(None, max_length=40), flight_set: str | None = Query(None, max_length=36),
               date_from: date | None = None, date_to: date | None = None,
               q: str | None = Query(None, max_length=100), sort: str = Query("captured", pattern="^(captured|uploaded)$")
               ) -> dict:
        """Media files of the workspace with ingest information, newest first."""
        try:
            extra, params = build_filters(category, drone, flight_set, date_from, date_to, q)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        order = "COALESCE(i.captured_at, FROM_UNIXTIME(f.create_time / 1000)) DESC, f.id DESC" if sort == "captured" \
            else "f.create_time DESC, f.id DESC"
        base = (f" FROM media_file f LEFT JOIN {ingest_table} i ON i.file_id = f.file_id"
                f" WHERE f.workspace_id = %s{extra}")
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS n" + base, [user.workspace_id, *params])
                total = cur.fetchone()["n"]
                cur.execute(
                    "SELECT f.file_id, f.file_name, f.object_key, f.drone AS pilot_drone, f.payload, f.is_original,"
                    " f.create_time, f.file_path, i.category, i.band, i.drone, i.flight_set_id, i.captured_at"
                    + base + f" ORDER BY {order} LIMIT %s OFFSET %s",
                    [user.workspace_id, *params, page_size, (page - 1) * page_size])
                rows = cur.fetchall()
        finally:
            conn.close()
        return {"total": total, "page": page, "page_size": page_size, "list": [{
            "file_id": r["file_id"], "file_name": r["file_name"], "object_key": r["object_key"],
            "file_path": r["file_path"], "payload": r["payload"], "is_original": bool(r["is_original"]),
            "drone": r["drone"] or r["pilot_drone"] or "", "pilot_drone": r["pilot_drone"] or "",
            "category": r["category"], "band": r["band"], "flight_set_id": r["flight_set_id"],
            "captured_at": _iso(r["captured_at"]),
            "uploaded_at": _iso(datetime.fromtimestamp(r["create_time"] / 1000, tz=timezone.utc).replace(tzinfo=None)),
        } for r in rows]}

    @router.get("/media/facets")
    def facets(user: User = Depends(current_user)) -> dict:
        """Filter values with counts: categories, drones (name from the device list or the camera model),
        capture date range."""
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COALESCE(i.category, 'other') AS category, COUNT(*) AS n FROM media_file f"
                            f" LEFT JOIN {ingest_table} i ON i.file_id = f.file_id WHERE f.workspace_id = %s"
                            f" GROUP BY 1", (user.workspace_id,))
                categories = {r["category"]: r["n"] for r in cur.fetchall()}
                cur.execute(f"SELECT COALESCE(NULLIF(i.drone, ''), f.drone, '') AS drone, COUNT(*) AS n,"
                            f" MIN(i.captured_at) AS first, MAX(i.captured_at) AS last FROM media_file f"
                            f" LEFT JOIN {ingest_table} i ON i.file_id = f.file_id WHERE f.workspace_id = %s"
                            f" GROUP BY 1 ORDER BY n DESC", (user.workspace_id,))
                drones = cur.fetchall()
                cur.execute("SELECT device_sn, nickname, device_name FROM manage_device WHERE workspace_id = %s",
                            (user.workspace_id,))
                names = {r["device_sn"]: r["nickname"] or r["device_name"] for r in cur.fetchall()}
                cur.execute(f"SELECT MIN(i.captured_at) AS first, MAX(i.captured_at) AS last FROM {ingest_table} i"
                            f" WHERE i.workspace_id = %s", (user.workspace_id,))
                span = cur.fetchone()
        finally:
            conn.close()
        with db.transaction(settings) as cur:
            cur.execute("SELECT i.drone, JSON_UNQUOTE(JSON_EXTRACT(m.meta, '$.model')) AS model, COUNT(*) AS n"
                        " FROM media_ingest i JOIN media_meta m ON m.file_id = i.file_id WHERE i.workspace_id = %s"
                        " GROUP BY 1, 2", (user.workspace_id,))
            models: dict[str, str] = {}
            for r in sorted(cur.fetchall(), key=lambda r: r["n"]):
                if r["model"]:
                    models[r["drone"] or ""] = r["model"]
        return {"categories": categories,
                "drones": [{"serial": d["drone"], "name": names.get(d["drone"]) or models.get(d["drone"]) or "",
                            "known_device": d["drone"] in names, "files": d["n"],
                            "first": _iso(d["first"]), "last": _iso(d["last"])} for d in drones],
                "first": _iso(span["first"]), "last": _iso(span["last"])}

    @router.get("/flight-sets-summary")
    def flight_set_summary(user: User = Depends(current_user)) -> list[dict]:
        """Every flight set with statistics, classification, PPK availability and sample photos."""
        with db.transaction(settings) as cur:
            cur.execute("SELECT * FROM flight_set WHERE workspace_id = %s ORDER BY first_capture DESC",
                        (user.workspace_id,))
            sets = cur.fetchall()
            cur.execute("SELECT i.flight_set_id, i.file_id, i.category, m.meta FROM media_ingest i"
                        " LEFT JOIN media_meta m ON m.file_id = i.file_id WHERE i.workspace_id = %s"
                        " AND i.flight_set_id IS NOT NULL", (user.workspace_id,))
            rows = cur.fetchall()
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT device_sn, nickname, device_name FROM manage_device WHERE workspace_id = %s",
                            (user.workspace_id,))
                names = {r["device_sn"]: r["nickname"] or r["device_name"] for r in cur.fetchall()}
                cur.execute(f"SELECT i.flight_set_id, f.object_key, f.file_path FROM media_file f"
                            f" JOIN {ingest_table} i ON i.file_id = f.file_id WHERE f.workspace_id = %s"
                            f" AND f.object_key LIKE 'wayline/sdimport-%%'", (user.workspace_id,))
                sd_folders: dict[str, set[str]] = {}
                for r in cur.fetchall():
                    m = re.match(r"^wayline/sdimport-([0-9A-Za-z-]+)/", r["object_key"])
                    folder = re.sub(r"^DCIM(/|$)", "", r["file_path"] or "")
                    sd_folders.setdefault(r["flight_set_id"], set()).add(f"ppk/sdimport-{m.group(1)}/{folder}".rstrip("/") + "/")
        finally:
            conn.close()
        ppk_dirs: set[str] = set()
        if storage is not None:
            for obj in storage.internal.list_objects(settings.media_bucket, prefix="ppk/", recursive=True):
                if obj.object_name.upper().endswith(".OBS"):
                    ppk_dirs.add(obj.object_name.rsplit("/", 1)[0] + "/")
        by_set: dict[str, list[dict]] = {}
        for r in rows:
            by_set.setdefault(r["flight_set_id"], []).append(r)
        out = []
        for s_ in sets:
            files = by_set.get(s_["id"], [])
            metas = [json.loads(f["meta"]) for f in files if f["meta"]]
            metas = [m for m in metas if "error" not in m]
            models = sorted({m.get("model") for m in metas if m.get("model")})
            summary = set_summary(metas)
            preview_cats = ("visible", "rgb", "thermal", "multispectral")
            samples = [f["file_id"] for c in preview_cats for f in files if f["category"] == c][:4]
            out.append({
                "id": s_["id"], "drone": s_["drone"], "drone_name": names.get(s_["drone"]) or "/".join(models),
                "models": models, "kind": s_["kind"], "first_capture": _iso(s_["first_capture"]),
                "last_capture": _iso(s_["last_capture"]),
                "minutes": round((s_["last_capture"] - s_["first_capture"]).total_seconds() / 60, 1),
                "shots": s_["shots"], "files": s_["files"],
                "by_category": dict(sorted({c: sum(1 for f in files if f["category"] == c)
                                            for c in {f["category"] for f in files}}.items())),
                "class": classify_set(summary["rel_alt_median"], summary["nadir_share"], s_["shots"]),
                "ppk": bool(sd_folders.get(s_["id"], set()) & ppk_dirs),
                "samples": samples, **summary})
        return out

    @router.post("/flight-sets/{set_id}/mapping-job", status_code=201)
    def flight_set_job(set_id: str, body: dict, user: User = Depends(current_user)) -> dict:
        """Mapping job from the photos of a flight set: RGB/visible JPGs, or the band TIFs for multispectral."""
        import uuid
        from .models import PROFILES
        name = str(body.get("name") or "").strip()[:200]
        profile = body.get("profile") or "standard"
        if not name or profile not in PROFILES:
            raise HTTPException(status_code=422, detail="Name und gueltiges Profil erforderlich")
        cats = ("multispectral",) if profile == "multispectral" else ("rgb", "visible")
        with db.transaction(settings) as cur:
            cur.execute("SELECT i.file_id, i.band FROM media_ingest i JOIN flight_set s ON s.id = i.flight_set_id"
                        " WHERE i.flight_set_id = %s AND s.workspace_id = %s AND i.category IN %s",
                        (set_id, user.workspace_id, cats))
            files = [r["file_id"] for r in cur.fetchall() if profile != "multispectral" or r["band"] != "F"]
        if not files:
            raise HTTPException(status_code=422, detail="Keine passenden Bilder in diesem Flugsatz")
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT object_key, file_name FROM media_file WHERE workspace_id = %s AND file_id IN ("
                            + ",".join(["%s"] * len(files)) + ")", [user.workspace_id, *files])
                keys = sorted(r["object_key"] for r in cur.fetchall()
                              if r["file_name"].upper().endswith((".JPG", ".JPEG", ".TIF", ".TIFF")))
        finally:
            conn.close()
        if len(keys) > 5000:
            raise HTTPException(status_code=422, detail="Mehr als 5000 Bilder: bitte aufteilen")
        job_id = str(uuid.uuid4())
        options = {"profile": profile, "odm": dict(PROFILES[profile]), "flight_set": set_id}
        with db.transaction(settings) as cur:
            cur.execute("INSERT INTO mapping_job (id, workspace_id, name, status, image_keys, options, created_by)"
                        " VALUES (%s, %s, %s, 'QUEUED', %s, %s, %s)",
                        (job_id, user.workspace_id, name, json.dumps(keys), json.dumps(options), user.username))
        return {"id": job_id, "name": name, "image_count": len(keys), "profile": profile}

    @router.get("/flight-sets/{set_id}/positions")
    def flight_set_positions(set_id: str, user: User = Depends(current_user)) -> dict:
        """Photo positions of one flight set for the map (one per shot, at most 3000)."""
        with db.transaction(settings) as cur:
            cur.execute("SELECT i.shot_key, i.category, i.captured_at, m.meta FROM media_ingest i"
                        " JOIN flight_set s ON s.id = i.flight_set_id LEFT JOIN media_meta m ON m.file_id = i.file_id"
                        " WHERE i.flight_set_id = %s AND s.workspace_id = %s ORDER BY i.captured_at",
                        (set_id, user.workspace_id))
            rows = cur.fetchall()
        if not rows:
            raise HTTPException(status_code=404, detail="Flugsatz nicht gefunden")
        # one point per shot, coloured "thermal" when the shot has a thermal photo (else by its first file)
        thermal_shots = {r["shot_key"] for r in rows if r["category"] == "thermal" and r["shot_key"]}
        seen, points = set(), []
        for r in rows:
            meta = json.loads(r["meta"]) if r["meta"] else {}
            if meta.get("lat") is None or (r["shot_key"] and r["shot_key"] in seen):
                continue
            seen.add(r["shot_key"])
            category = "thermal" if r["shot_key"] in thermal_shots else r["category"]
            points.append([round(meta["lat"], 7), round(meta["lon"], 7), category,
                           round(meta["rel_alt"], 1) if meta.get("rel_alt") is not None else None])
        step = max(1, math.ceil(len(points) / 3000))
        return {"points": points[::step], "total": len(points)}

    return router
