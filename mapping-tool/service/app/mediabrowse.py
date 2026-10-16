"""Media list for the web UI with filters (category, drone, flight set, capture date, name).

The DJI backend's media list knows only what Pilot 2 reported; this one joins the ingest results (category from
name and camera model, drone from the aircraft serial, flight set, capture time). Downloads stay with the
DJI backend (file_id).
"""
import re
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


def build_router(settings):
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

    return router
