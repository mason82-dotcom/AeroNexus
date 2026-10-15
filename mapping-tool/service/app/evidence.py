"""Evidence register (Beweissicherung): SHA-256 of every media file uploaded by Pilot 2.

A background sealer hashes new objects from MinIO once and appends one row per file to
`evidence_record`. Rows form a hash chain: record_hash = SHA-256 over the canonical form of
the row including prev_hash (the record_hash of the row before). Changing a stored hash,
any metadata field, or deleting/reordering rows breaks verify_chain(). The register proves
that a file is byte-identical to what was stored when it was sealed; it cannot prove what
happened before the upload. Rows are never updated or deleted.
"""
import hashlib
import logging
import threading
from datetime import datetime, timezone

log = logging.getLogger("mapping.evidence")

GENESIS = "0" * 64
FORMAT = "aeronexus-evidence-v1"
CHUNK = 1024 * 1024
LOCK_NAME = "aeronexus_evidence_chain"
BATCH = 20                # files per sealer round
INTERVAL_SECONDS = 60     # sealer period


# ------------------------------------------------------------------ pure core

def _iso(value: datetime | None) -> str:
    """UTC, millisecond precision, '' for None (MySQL DATETIME(3) round-trips exactly)."""
    if value is None:
        return ""
    if value.tzinfo is not None:
        value = value.astimezone(timezone.utc).replace(tzinfo=None)
    return value.strftime("%Y-%m-%dT%H:%M:%S.") + f"{value.microsecond // 1000:03d}Z"


FIELDS = ("seq", "file_id", "workspace_id", "file_name", "object_key", "size_bytes", "sha256",
          "drone", "payload", "uploaded_at", "sealed_at", "prev_hash")


def canonical(record: dict) -> bytes:
    """Unambiguous byte form of a row: format tag, then each field as <length>:<value>."""
    parts = [FORMAT]
    for name in FIELDS:
        value = record[name]
        if isinstance(value, datetime) or value is None:
            value = _iso(value)
        text = str(value)
        parts.append(f"{len(text.encode('utf-8'))}:{text}")
    return "\n".join(parts).encode("utf-8")


def record_hash(record: dict) -> str:
    return hashlib.sha256(canonical(record)).hexdigest()


def verify_chain(records: list[dict]) -> dict:
    """Check rows in seq order: contiguous seq from 1, links and record hashes.
    Returns {"status": "ok"|"broken"|"empty", "records": n, "head": hash, ...}."""
    if not records:
        return {"status": "empty", "records": 0, "head": GENESIS}
    prev = GENESIS
    for index, row in enumerate(records, start=1):
        problem = None
        if row["seq"] != index:
            problem = "sequence_gap"
        elif row["prev_hash"] != prev:
            problem = "link_mismatch"
        elif record_hash(row) != row["record_hash"]:
            problem = "record_modified"
        if problem:
            return {"status": "broken", "records": len(records), "broken_at": row["seq"],
                    "file_id": row["file_id"], "reason": problem}
        prev = row["record_hash"]
    return {"status": "ok", "records": len(records), "head": prev}


def next_record(previous: dict | None, media: dict, size: int, sha256: str,
                sealed_at: datetime) -> dict:
    """Row to append after `previous` (None for the first row)."""
    record = {
        "seq": (previous["seq"] + 1) if previous else 1,
        "file_id": media["file_id"],
        "workspace_id": media["workspace_id"],
        "file_name": media["file_name"],
        "object_key": media["object_key"],
        "size_bytes": size,
        "sha256": sha256,
        "drone": media.get("drone") or "",
        "payload": media.get("payload") or "",
        "uploaded_at": media.get("uploaded_at"),
        "sealed_at": sealed_at.replace(microsecond=sealed_at.microsecond // 1000 * 1000),
        "prev_hash": previous["record_hash"] if previous else GENESIS,
    }
    record["record_hash"] = record_hash(record)
    return record


def hash_stream(chunks) -> tuple[int, str]:
    """(size, SHA-256 hex) of an iterable of byte chunks."""
    digest = hashlib.sha256()
    size = 0
    for chunk in chunks:
        digest.update(chunk)
        size += len(chunk)
    return size, digest.hexdigest()


# ------------------------------------------------------------- MinIO + MySQL

def hash_object(storage, bucket: str, key: str) -> tuple[int, str]:
    resp = storage.internal.get_object(bucket, key)
    try:
        return hash_stream(resp.stream(CHUNK))
    finally:
        resp.close()
        resp.release_conn()


COLUMNS = ", ".join(FIELDS + ("record_hash",))


def _unsealed(cur, settings, limit: int) -> list[dict]:
    cur.execute(
        f"SELECT m.file_id, m.workspace_id, m.file_name, m.object_key, m.drone, m.payload,"
        f" m.create_time FROM `{settings.media_database}`.media_file m"
        f" LEFT JOIN `{settings.mysql_database}`.evidence_record e ON e.file_id = m.file_id"
        f" WHERE e.file_id IS NULL AND m.object_key <> '' ORDER BY m.id LIMIT %s", (limit,))
    rows = cur.fetchall()
    for row in rows:
        ms = row.pop("create_time")
        row["uploaded_at"] = (datetime.fromtimestamp(ms / 1000, tz=timezone.utc).replace(tzinfo=None)
                              if ms else None)
    return rows


def seal_pending(settings, storage, db, limit: int = BATCH) -> int:
    """Hash and register up to `limit` unsealed media files. Returns the number sealed."""
    conn = db.connect(settings, settings.mysql_database)
    sealed = 0
    try:
        with conn.cursor() as cur:
            # One chain writer at a time, also across workers or overlapping restarts.
            cur.execute("SELECT GET_LOCK(%s, 0) AS got", (LOCK_NAME,))
            if not cur.fetchone()["got"]:
                return 0
            try:
                pending = _unsealed(cur, settings, limit)
                conn.commit()
                for media in pending:
                    try:
                        size, digest = hash_object(storage, settings.media_bucket, media["object_key"])
                    except Exception as exc:  # object not uploaded yet, MinIO down: retry next round
                        log.warning("evidence: %s not hashable yet: %s", media["file_name"], exc)
                        continue
                    cur.execute(f"SELECT {COLUMNS} FROM evidence_record ORDER BY seq DESC LIMIT 1")
                    previous = cur.fetchone()
                    record = next_record(previous, media, size, digest, datetime.now(timezone.utc).replace(tzinfo=None))
                    marks = ", ".join(["%s"] * (len(FIELDS) + 1))
                    cur.execute(f"INSERT INTO evidence_record ({COLUMNS}) VALUES ({marks})",
                                [record[f] for f in FIELDS + ("record_hash",)])
                    conn.commit()
                    sealed += 1
                    log.info("evidence: sealed #%d %s sha256=%s", record["seq"], media["file_name"], digest[:16])
            finally:
                cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK_NAME,))
                conn.commit()
    finally:
        conn.close()
    return sealed


def load_chain(settings, db) -> list[dict]:
    with db.transaction(settings) as cur:
        cur.execute(f"SELECT {COLUMNS} FROM evidence_record ORDER BY seq")
        return cur.fetchall()


def start_sealer(settings, storage, db, stop: threading.Event) -> threading.Thread:
    """Daemon thread: seal new media every INTERVAL_SECONDS until `stop` is set."""
    def run():
        while not stop.is_set():
            try:
                while seal_pending(settings, storage, db) == BATCH and not stop.is_set():
                    pass  # backlog: keep going without waiting
            except Exception as exc:
                log.warning("evidence sealer round failed: %s", exc)
            stop.wait(INTERVAL_SECONDS)
    thread = threading.Thread(target=run, name="evidence-sealer", daemon=True)
    thread.start()
    return thread


# ------------------------------------------------------------------ web API

def file_verdict(record: dict, previous: dict | None, current: tuple[int, str] | None) -> dict:
    """Pure: verdict for one sealed file. current = (size, sha256) now in MinIO, None if missing."""
    record_ok = record_hash(record) == record["record_hash"]
    link_ok = record["prev_hash"] == (previous["record_hash"] if previous else GENESIS)
    if previous is None and record["seq"] != 1:
        link_ok = False
    if current is None:
        file_state = "missing"
    elif current == (record["size_bytes"], record["sha256"]):
        file_state = "intact"
    else:
        file_state = "modified"
    return {"file": file_state, "record": "ok" if record_ok and link_ok else "broken",
            "sealed_sha256": record["sha256"], "current_sha256": current[1] if current else None}


def public_record(row: dict) -> dict:
    return {"seq": row["seq"], "file_id": row["file_id"], "file_name": row["file_name"],
            "size_bytes": row["size_bytes"], "sha256": row["sha256"], "drone": row["drone"],
            "payload": row["payload"], "uploaded_at": _iso(row["uploaded_at"]) or None,
            "sealed_at": _iso(row["sealed_at"]), "record_hash": row["record_hash"]}


def build_router(settings, storage):
    import csv
    import io

    from fastapi import APIRouter, Depends, HTTPException, Query
    from fastapi.responses import Response
    from pydantic import BaseModel, Field

    from . import db
    from .auth import User, user_dependency

    router = APIRouter(prefix="/api/mapping/evidence")
    current_user = user_dependency(settings)

    class StatusRequest(BaseModel):
        file_ids: list[str] = Field(min_length=1, max_length=500)

    def rows_for(cur, user: User, file_ids: list[str]) -> dict:
        marks = ",".join(["%s"] * len(file_ids))
        cur.execute(f"SELECT {COLUMNS} FROM evidence_record WHERE workspace_id = %s AND file_id IN ({marks})",
                    [user.workspace_id, *file_ids])
        return {r["file_id"]: r for r in cur.fetchall()}

    def previous_of(cur, row: dict) -> dict | None:
        if row["seq"] <= 1:
            return None
        cur.execute(f"SELECT {COLUMNS} FROM evidence_record WHERE seq = %s", (row["seq"] - 1,))
        return cur.fetchone()

    def current_hash(row: dict) -> tuple[int, str] | None:
        try:
            return hash_object(storage, settings.media_bucket, row["object_key"])
        except Exception as exc:
            if getattr(exc, "code", "") in ("NoSuchKey", "NoSuchBucket"):
                return None
            raise HTTPException(status_code=503, detail="Medienablage nicht erreichbar") from exc

    @router.post("/status")
    def status(body: StatusRequest, user: User = Depends(current_user)) -> dict:
        """Seal state for the media list: sealed rows, others null (not sealed yet)."""
        ids = list(dict.fromkeys(body.file_ids))
        with db.transaction(settings) as cur:
            found = rows_for(cur, user, ids)
        return {fid: public_record(found[fid]) if fid in found else None for fid in ids}

    @router.get("/chain")
    def chain(user: User = Depends(current_user)) -> dict:
        """Integrity of the whole register (all workspaces form one chain)."""
        result = verify_chain(load_chain(settings, db))
        result.pop("file_id", None)  # never name another workspace's file
        return result

    @router.get("/{file_id}/verify")
    def verify(file_id: str, user: User = Depends(current_user)) -> dict:
        """Re-hash the stored object and check its register row."""
        with db.transaction(settings) as cur:
            row = rows_for(cur, user, [file_id]).get(file_id)
            if not row:
                raise HTTPException(status_code=404, detail="Datei ist (noch) nicht versiegelt")
            previous = previous_of(cur, row)
        return {**public_record(row), **file_verdict(row, previous, current_hash(row))}

    @router.get("/report")
    def report(file_ids: str = Query(..., max_length=20000), fmt: str = Query("json", pattern="^(json|csv)$"),
               user: User = Depends(current_user)):
        """Prüfbericht: every requested file re-hashed now, plus the chain state."""
        ids = list(dict.fromkeys(i for i in file_ids.split(",") if i))[:500]
        if not ids:
            raise HTTPException(status_code=422, detail="Keine Dateien angegeben")
        with db.transaction(settings) as cur:
            found = rows_for(cur, user, ids)
            previous = {fid: previous_of(cur, row) for fid, row in found.items()}
        files = []
        for fid in ids:
            row = found.get(fid)
            if not row:
                files.append({"file_id": fid, "file": "not_sealed"})
                continue
            files.append({**public_record(row), **file_verdict(row, previous[fid], current_hash(row))})
        chain_state = verify_chain(load_chain(settings, db))
        chain_state.pop("file_id", None)
        generated = _iso(datetime.now(timezone.utc))
        if fmt == "json":
            return {"format": FORMAT, "generated_at": generated, "workspace_id": user.workspace_id,
                    "generated_by": user.username, "chain": chain_state, "files": files}
        out = io.StringIO()
        columns = ["seq", "file_id", "file_name", "size_bytes", "sha256", "current_sha256", "file", "record",
                   "drone", "payload", "uploaded_at", "sealed_at", "record_hash"]
        out.write(f"# {FORMAT}; generated_at={generated}; by={user.username}; chain={chain_state['status']};"
                  f" records={chain_state['records']}; head={chain_state.get('head', '')}\n")
        writer = csv.DictWriter(out, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for f in files:
            writer.writerow(f)
        return Response(content=out.getvalue(), media_type="text/csv; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="pruefbericht-{generated[:10]}.csv"'})

    return router
