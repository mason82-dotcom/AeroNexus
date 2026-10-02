"""REST endpoints: web UI (/api/mapping/...) and compute agent (/api/mapping/agent/...)."""
import json
import logging
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response
from fastapi.responses import RedirectResponse

from . import db
from .auth import User, agent_dependency, tile_user_dependency, user_dependency
from .config import Settings
from .models import PROFILES, Claim, Complete, Fail, Heartbeat, JobCreate, RouteCopy, UploadUrls, AgentRef
from .storage import Storage
from . import wpml

log = logging.getLogger("mapping.api")

ACTIVE = ("CLAIMED", "RUNNING")
IMAGE_EXT = (".jpg", ".jpeg", ".tif", ".tiff", ".dng")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat(timespec="seconds") + "Z" if value else None


def _job_out(row: dict) -> dict:
    return {
        "id": row["id"], "name": row["name"], "status": row["status"],
        "image_count": len(json.loads(row["image_keys"])), "options": json.loads(row["options"]),
        "created_by": row["created_by"], "agent_id": row["agent_id"],
        "lease_until": _iso(row["lease_until"]), "attempts": row["attempts"],
        "progress": float(row["progress"]), "message": row["message"], "error": row["error"],
        "created_at": _iso(row["created_at"]), "updated_at": _iso(row["updated_at"]),
        "finished_at": _iso(row["finished_at"]),
    }


def _layer_out(row: dict) -> dict:
    return {
        "id": row["id"], "job_id": row["job_id"], "name": row["name"], "type": row["layer_type"],
        "format": row["tile_format"], "min_zoom": row["min_zoom"], "max_zoom": row["max_zoom"],
        "bounds_wgs84": json.loads(row["bounds_wgs84"]) if row["bounds_wgs84"] else None,
        "crs": row["crs"], "opacity": float(row["opacity"]), "attribution": row["attribution"],
        "created_at": _iso(row["created_at"]),
        # relative to the service base URL; the web adds ?token=<x-auth-token>
        "tile_url": f"/api/mapping/layers/{row['id']}/tiles/{{z}}/{{x}}/{{y}}.{row['tile_format']}",
    }


def requeue_expired(cur, settings: Settings) -> int:
    """Expired leases -> QUEUED (or FAILED after max attempts). MySQL evaluates SET left to right,
    so every expression reads the old 'attempts' until it is incremented last."""
    return cur.execute(
        "UPDATE mapping_job SET"
        " status = IF(attempts + 1 >= %(max)s, 'FAILED', 'QUEUED'),"
        " error = IF(attempts + 1 >= %(max)s, CONCAT('lease expired ', attempts + 1, ' times'), error),"
        " finished_at = IF(attempts + 1 >= %(max)s, UTC_TIMESTAMP(3), NULL),"
        " message = CONCAT('lease of ', agent_id, ' expired'),"
        " agent_id = NULL, lease_until = NULL, updated_at = UTC_TIMESTAMP(3),"
        " attempts = attempts + 1"
        " WHERE status IN ('CLAIMED', 'RUNNING') AND lease_until < UTC_TIMESTAMP(3)",
        {"max": settings.max_lease_attempts},
    )


def build_router(settings: Settings, storage: Storage) -> APIRouter:
    router = APIRouter(prefix="/api/mapping")
    current_user = user_dependency(settings)
    tile_user = tile_user_dependency(settings)
    agent = agent_dependency(settings)

    def image_urls(keys: list[str]) -> list[dict]:
        ttl = settings.presign_ttl_seconds
        return [{"key": k, "url": storage.presign_get(settings.media_bucket, k), "expires_in": ttl}
                for k in keys]

    def lock_active_job(cur, job_id: str, agent_id: str) -> dict:
        cur.execute("SELECT * FROM mapping_job WHERE id = %s FOR UPDATE", (job_id,))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="job not found")
        cur.execute("SELECT UTC_TIMESTAMP(3) AS now")
        now = cur.fetchone()["now"]
        if row["status"] not in ACTIVE or row["agent_id"] != agent_id or row["lease_until"] < now:
            raise HTTPException(status_code=409, detail="job is not leased to this agent")
        return row

    def lease(seconds: int | None) -> int:
        return seconds or settings.default_lease_seconds

    # ---------------------------------------------------------------- web UI

    @router.get("/media")
    def list_media(user: User = Depends(current_user),
                   page: int = Query(1, ge=1), page_size: int = Query(100, ge=1, le=500)) -> dict:
        """Images uploaded by Pilot 2 (DJI media library), newest first."""
        ext_sql = " OR ".join(["LOWER(file_name) LIKE %s"] * len(IMAGE_EXT))
        params = [f"%{e}" for e in IMAGE_EXT]
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                where = f"workspace_id = %s AND ({ext_sql})"
                cur.execute(f"SELECT COUNT(*) AS n FROM media_file WHERE {where}", [user.workspace_id, *params])
                total = cur.fetchone()["n"]
                cur.execute(
                    f"SELECT file_id, file_name, object_key, drone, payload, is_original, create_time"
                    f" FROM media_file WHERE {where} ORDER BY create_time DESC LIMIT %s OFFSET %s",
                    [user.workspace_id, *params, page_size, (page - 1) * page_size])
                rows = cur.fetchall()
        finally:
            conn.close()
        return {"total": total, "page": page, "page_size": page_size, "list": [
            {**r, "is_original": bool(r["is_original"]),
             "create_time": _iso(datetime.fromtimestamp(r["create_time"] / 1000, tz=timezone.utc).replace(tzinfo=None))} for r in rows]}

    @router.post("/jobs", status_code=201)
    def create_job(body: JobCreate, user: User = Depends(current_user)) -> dict:
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                marks = ",".join(["%s"] * len(body.image_keys))
                cur.execute(f"SELECT object_key FROM media_file WHERE workspace_id = %s AND object_key IN ({marks})",
                            [user.workspace_id, *body.image_keys])
                known = {r["object_key"] for r in cur.fetchall()}
        finally:
            conn.close()
        unknown = [k for k in body.image_keys if k not in known]
        if unknown:
            raise HTTPException(status_code=422, detail={"unknown_image_keys": unknown[:50]})
        job_id = str(uuid.uuid4())
        options = {"profile": body.profile, "odm": {**PROFILES[body.profile], **body.odm_options}}
        with db.transaction(settings) as cur:
            cur.execute(
                "INSERT INTO mapping_job (id, workspace_id, name, status, image_keys, options, created_by)"
                " VALUES (%s, %s, %s, 'QUEUED', %s, %s, %s)",
                (job_id, user.workspace_id, body.name, json.dumps(body.image_keys), json.dumps(options),
                 user.username))
            cur.execute("SELECT * FROM mapping_job WHERE id = %s", (job_id,))
            row = cur.fetchone()
        log.info("job %s created by %s with %d images", job_id, user.username, len(body.image_keys))
        return _job_out(row)

    @router.get("/jobs")
    def list_jobs(user: User = Depends(current_user)) -> list[dict]:
        with db.transaction(settings) as cur:
            requeue_expired(cur, settings)
            cur.execute("SELECT * FROM mapping_job WHERE workspace_id = %s ORDER BY created_at DESC LIMIT 200",
                        (user.workspace_id,))
            return [_job_out(r) for r in cur.fetchall()]

    @router.get("/jobs/{job_id}")
    def get_job(job_id: str, user: User = Depends(current_user)) -> dict:
        with db.transaction(settings) as cur:
            requeue_expired(cur, settings)
            cur.execute("SELECT * FROM mapping_job WHERE id = %s AND workspace_id = %s", (job_id, user.workspace_id))
            row = cur.fetchone()
            if not row:
                raise HTTPException(status_code=404, detail="job not found")
            cur.execute("SELECT kind, object_key, sha256, size_bytes FROM mapping_result WHERE job_id = %s", (job_id,))
            results = cur.fetchall()
        return {**_job_out(row), "results": results}

    @router.get("/layers")
    def list_layers(user: User = Depends(current_user)) -> list[dict]:
        with db.transaction(settings) as cur:
            cur.execute("SELECT * FROM map_layer WHERE workspace_id = %s ORDER BY created_at DESC", (user.workspace_id,))
            return [_layer_out(r) for r in cur.fetchall()]

    @router.get("/layers/{layer_id}/tiles/{z}/{x}/{y}.{fmt}")
    def tile(layer_id: str, z: int, x: int, y: int, fmt: str, user: User = Depends(tile_user)):
        with db.transaction(settings) as cur:
            cur.execute("SELECT * FROM map_layer WHERE id = %s AND workspace_id = %s", (layer_id, user.workspace_id))
            layer = cur.fetchone()
        if not layer or fmt != layer["tile_format"]:
            raise HTTPException(status_code=404, detail="layer not found")
        if not layer["min_zoom"] <= z <= layer["max_zoom"] or not (0 <= x < 2 ** z and 0 <= y < 2 ** z):
            return Response(status_code=204)
        key = f"{layer['tile_prefix']}/{z}/{x}/{y}.{fmt}"
        return RedirectResponse(storage.presign_get(layer["bucket"], key, seconds=300), status_code=302)

    # ---------------------------------------------------------------- flight routes (wayline library)

    def wayline_row(user: User, wayline_id: str) -> dict:
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT wayline_id, name, object_key, template_types, drone_model_key FROM wayline_file"
                            " WHERE workspace_id = %s AND wayline_id = %s", (user.workspace_id, wayline_id))
                row = cur.fetchone()
        finally:
            conn.close()
        if not row:
            raise HTTPException(status_code=404, detail="route not found")
        return row

    def load_kmz(row: dict) -> bytes:
        resp = storage.internal.get_object(settings.media_bucket, row["object_key"])
        try:
            return resp.read()
        finally:
            resp.close()
            resp.release_conn()

    @router.get("/waylines/{wayline_id}")
    def get_wayline(wayline_id: str, user: User = Depends(current_user)) -> dict:
        row = wayline_row(user, wayline_id)
        try:
            route = wpml.parse(load_kmz(row))
        except wpml.WpmlError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {
            "id": row["wayline_id"], "name": row["name"], "drone_model_key": row["drone_model_key"],
            "template_type": route.template_type, "wpml_namespace": route.wpml_ns,
            "editable": route.template_type in wpml.EDITABLE_TYPES and route.polygon is not None,
            "polygon": route.polygon, "params": route.params, "flight_path": route.flight_path,
        }

    @router.post("/waylines/{wayline_id}/copy", status_code=201)
    def copy_wayline(wayline_id: str, body: RouteCopy, user: User = Depends(current_user),
                     x_auth_token: str = Header()) -> dict:
        row = wayline_row(user, wayline_id)
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM wayline_file WHERE workspace_id = %s AND name = %s",
                            (user.workspace_id, body.name))
                if cur.fetchone():
                    raise HTTPException(status_code=409, detail="a route with this name already exists")
        finally:
            conn.close()
        try:
            kmz = wpml.edit_copy(load_kmz(row), body.polygon, body.params.model_dump())
        except wpml.WpmlError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        # import through the DJI backend, exactly like a manual upload in the web UI
        boundary = uuid.uuid4().hex
        payload = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{body.name}.kmz\"\r\n"
                   f"Content-Type: application/vnd.google-earth.kmz\r\n\r\n").encode() + kmz + f"\r\n--{boundary}--\r\n".encode()
        req = urllib.request.Request(
            f"{settings.dji_api_url}/wayline/api/v1/workspaces/{user.workspace_id}/waylines/file/upload",
            data=payload, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}", "x-auth-token": x_auth_token})
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                result = json.loads(resp.read() or b"{}")
        except urllib.error.URLError as exc:
            raise HTTPException(status_code=502, detail=f"DJI backend not reachable: {exc}") from exc
        if result.get("code") != 0:
            raise HTTPException(status_code=502, detail=f"DJI backend rejected the route: {result.get('message')}")
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT wayline_id FROM wayline_file WHERE workspace_id = %s AND name = %s",
                            (user.workspace_id, body.name))
                new = cur.fetchone()
        finally:
            conn.close()
        log.info("route %s copied to '%s' by %s", wayline_id, body.name, user.username)
        return {"id": new["wayline_id"] if new else None, "name": body.name}

    # ---------------------------------------------------------------- compute agent

    @router.post("/agent/claim", dependencies=[Depends(agent)])
    def claim(body: Claim):
        with db.transaction(settings) as cur:
            requeue_expired(cur, settings)
            cur.execute("SELECT * FROM mapping_job WHERE status = 'QUEUED' ORDER BY created_at, id"
                        " LIMIT 1 FOR UPDATE SKIP LOCKED")
            row = cur.fetchone()
            if not row:
                return Response(status_code=204)
            cur.execute(
                "UPDATE mapping_job SET status = 'CLAIMED', agent_id = %s,"
                " lease_until = UTC_TIMESTAMP(3) + INTERVAL %s SECOND, claimed_at = UTC_TIMESTAMP(3),"
                " message = %s, updated_at = UTC_TIMESTAMP(3) WHERE id = %s",
                (body.agent_id, lease(body.lease_seconds), f"claimed by {body.agent_id}"[:500], row["id"]))
            cur.execute("SELECT * FROM mapping_job WHERE id = %s", (row["id"],))
            row = cur.fetchone()
        log.info("job %s claimed by %s (capabilities %s)", row["id"], body.agent_id, body.capabilities)
        job = _job_out(row)
        return {
            "job": {k: job[k] for k in ("id", "name", "status", "options", "attempts", "lease_until")},
            "images": image_urls(json.loads(row["image_keys"])),
            "results_prefix": f"{settings.results_bucket}/{row['id']}/",
        }

    @router.post("/agent/jobs/{job_id}/image-urls", dependencies=[Depends(agent)])
    def refresh_image_urls(job_id: str, body: AgentRef) -> dict:
        with db.transaction(settings) as cur:
            row = lock_active_job(cur, job_id, body.agent_id)
        return {"images": image_urls(json.loads(row["image_keys"]))}

    @router.post("/agent/jobs/{job_id}/heartbeat", dependencies=[Depends(agent)])
    def heartbeat(job_id: str, body: Heartbeat) -> dict:
        with db.transaction(settings) as cur:
            lock_active_job(cur, job_id, body.agent_id)
            cur.execute(
                "UPDATE mapping_job SET status = 'RUNNING', progress = %s, message = %s,"
                " lease_until = UTC_TIMESTAMP(3) + INTERVAL %s SECOND, updated_at = UTC_TIMESTAMP(3) WHERE id = %s",
                (body.progress, body.message, lease(body.lease_seconds), job_id))
            cur.execute("SELECT status, lease_until FROM mapping_job WHERE id = %s", (job_id,))
            row = cur.fetchone()
        return {"status": row["status"], "lease_until": _iso(row["lease_until"])}

    @router.post("/agent/jobs/{job_id}/upload-urls", dependencies=[Depends(agent)])
    def upload_urls(job_id: str, body: UploadUrls) -> dict:
        with db.transaction(settings) as cur:
            lock_active_job(cur, job_id, body.agent_id)
        return {"urls": {p: storage.presign_put(settings.results_bucket, f"{job_id}/{p}") for p in body.paths},
                "expires_in": settings.presign_ttl_seconds}

    @router.post("/agent/jobs/{job_id}/complete", dependencies=[Depends(agent)])
    def complete(job_id: str, body: Complete) -> dict:
        manifest = body.manifest
        if manifest.tiles and manifest.tiles.minzoom > manifest.tiles.maxzoom:
            raise HTTPException(status_code=422, detail="tiles.minzoom > tiles.maxzoom")
        bucket = settings.results_bucket
        # checks run before the row lock: they only read MinIO
        missing = [f.path for f in manifest.files if not storage.exists(bucket, f"{job_id}/{f.path}")]
        if manifest.tiles and not storage.has_prefix(bucket, f"{job_id}/{manifest.tiles.path}/"):
            missing.append(f"{manifest.tiles.path}/")
        if missing:
            raise HTTPException(status_code=422, detail={"missing": missing[:100]})
        layer_id = None
        with db.transaction(settings) as cur:
            job = lock_active_job(cur, job_id, body.agent_id)
            for f in manifest.files:
                cur.execute(
                    "INSERT INTO mapping_result (job_id, kind, object_key, sha256, size_bytes) VALUES (%s, %s, %s, %s, %s)"
                    " ON DUPLICATE KEY UPDATE kind = VALUES(kind), sha256 = VALUES(sha256), size_bytes = VALUES(size_bytes)",
                    (job_id, f.kind, f"{job_id}/{f.path}", f.sha256, f.size))
            if manifest.tiles:
                layer_id = str(uuid.uuid4())
                cur.execute(
                    "INSERT INTO map_layer (id, workspace_id, job_id, name, layer_type, bucket, tile_prefix,"
                    " tile_format, min_zoom, max_zoom, bounds_wgs84, crs) VALUES"
                    " (%s, %s, %s, %s, 'xyz', %s, %s, %s, %s, %s, %s, %s)",
                    (layer_id, job["workspace_id"], job_id, job["name"], bucket, f"{job_id}/{manifest.tiles.path}",
                     manifest.tiles.format, manifest.tiles.minzoom, manifest.tiles.maxzoom,
                     json.dumps(manifest.bounds_wgs84) if manifest.bounds_wgs84 else None, manifest.crs))
            cur.execute(
                "UPDATE mapping_job SET status = 'DONE', progress = 100, message = 'completed', lease_until = NULL,"
                " finished_at = UTC_TIMESTAMP(3), updated_at = UTC_TIMESTAMP(3) WHERE id = %s", (job_id,))
        log.info("job %s completed by %s: %d file(s), layer %s", job_id, body.agent_id, len(manifest.files), layer_id)
        return {"status": "DONE", "layer_id": layer_id}

    @router.post("/agent/jobs/{job_id}/fail", dependencies=[Depends(agent)])
    def fail(job_id: str, body: Fail) -> dict:
        with db.transaction(settings) as cur:
            lock_active_job(cur, job_id, body.agent_id)
            cur.execute(
                "UPDATE mapping_job SET status = 'FAILED', error = %s, message = 'failed', lease_until = NULL,"
                " finished_at = UTC_TIMESTAMP(3), updated_at = UTC_TIMESTAMP(3) WHERE id = %s", (body.error, job_id))
        log.info("job %s failed on %s", job_id, body.agent_id)
        return {"status": "FAILED"}

    return router
