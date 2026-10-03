"""Mission planning for remote controller operations (DJI Pilot 2 + RC).

With a remote controller DJI does not allow starting a mission from the cloud (Dock only), so a mission here is
the plan around the flight: when, where, purpose, routes (synced to Pilot 2), drone, pilot, and a pre-flight
checklist the pilot can tick on the RC. Status: planned -> ready (all checks done) -> flown -> evaluated,
or cancelled. Times are UTC in the API (ISO 8601 with offset accepted on input).
"""
import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator

from . import db
from .auth import User, user_dependency
from .config import Settings

log = logging.getLogger("mapping.missions")

PURPOSES = {"pv": "PV-Inspektion", "farming": "Farming (Multispektral)", "mapping": "Kartierung", "other": "Sonstiges"}
STATUSES = ("planned", "ready", "flown", "evaluated", "cancelled")
# manual transitions (planned <-> ready also happens automatically with the checklist)
TRANSITIONS = {
    "planned": {"ready", "flown", "cancelled"},
    "ready": {"planned", "flown", "cancelled"},
    "flown": {"evaluated", "ready"},
    "evaluated": {"flown"},
    "cancelled": {"planned"},
}

BASE_CHECKS = [
    ("weather", "Wetter gepr\u00fcft (Wind, Niederschlag, Sicht)"),
    ("airspace", "Luftraum und Geozonen gepr\u00fcft (z. B. dipul.de), ggf. Genehmigung"),
    ("owner", "Grundst\u00fcckseigent\u00fcmer bzw. Auftraggeber informiert"),
    ("batteries", "Akkus geladen (Drohne und Fernsteuerung)"),
    ("storage", "Speicherkarte mit ausreichend Platz"),
    ("route", "Route in Pilot 2 synchronisiert und vor dem Start gepr\u00fcft"),
    ("rth", "R\u00fcckkehrh\u00f6he und Startpunkt gepr\u00fcft"),
]
PURPOSE_CHECKS = {
    "pv": [("irradiance", "Einstrahlung \u00fcber 600 W/m\u00b2, Anlage unter Last, klarer Himmel"),
           ("thermal_settings", "W\u00e4rmebild als R-JPEG, Emissionsgrad und Palette gepr\u00fcft")],
    "farming": [("sunlight_sensor", "Sonnensensor frei, gleichm\u00e4\u00dfiges Licht (m\u00f6glichst mittags)")],
    "mapping": [("gcp", "Passpunkte ausgelegt bzw. RTK aktiv (falls ben\u00f6tigt)")],
    "other": [],
}


def new_checklist(purpose: str) -> list[dict]:
    return [{"key": k, "label": label, "done": False, "by": None, "at": None}
            for k, label in BASE_CHECKS + PURPOSE_CHECKS.get(purpose, [])]


def merge_checklist(old: list[dict], purpose: str) -> list[dict]:
    """New purpose: keep the state of items that exist in both lists."""
    done = {c["key"]: c for c in old}
    return [done.get(c["key"], c) for c in new_checklist(purpose)]


def tick(checklist: list[dict], key: str, done: bool, user: str, now: str) -> list[dict]:
    hit = False
    for c in checklist:
        if c["key"] == key:
            c.update(done=done, by=user if done else None, at=now if done else None)
            hit = True
    if not hit:
        raise KeyError(key)
    return checklist


def auto_status(status: str, checklist: list[dict]) -> str:
    """planned -> ready when every item is ticked, ready -> planned when one is unticked again."""
    complete = all(c["done"] for c in checklist)
    if status == "planned" and complete:
        return "ready"
    if status == "ready" and not complete:
        return "planned"
    return status


def check_transition(old: str, new: str) -> None:
    if new not in STATUSES:
        raise ValueError(f"Unbekannter Status {new}")
    if new != old and new not in TRANSITIONS[old]:
        raise ValueError(f"Status {old} kann nicht zu {new} wechseln")


def _utc(dt: datetime) -> datetime:
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc).replace(tzinfo=None)


class MissionIn(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    purpose: str = Field(pattern=r"^(pv|farming|mapping|other)$")
    planned_start: datetime
    duration_min: int | None = Field(default=None, ge=1, le=24 * 60)
    site: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=4000)
    drone_sn: str | None = Field(default=None, max_length=32)
    pilot: str | None = Field(default=None, max_length=64)
    wayline_ids: list[str] = Field(default_factory=list, max_length=20)

    @field_validator("title", "site")
    @classmethod
    def strip(cls, v: str) -> str:
        return v.strip()


class CheckIn(BaseModel):
    done: bool


class StatusIn(BaseModel):
    status: str


def _out(row: dict) -> dict:
    return {
        "id": row["id"], "title": row["title"], "purpose": row["purpose"],
        "purpose_label": PURPOSES.get(row["purpose"], row["purpose"]),
        "planned_start": row["planned_start"].replace(tzinfo=timezone.utc).isoformat().replace("+00:00", "Z"),
        "duration_min": row["duration_min"], "site": row["site"], "notes": row["notes"] or "",
        "drone_sn": row["drone_sn"], "pilot": row["pilot"],
        "wayline_ids": json.loads(row["wayline_ids"]) if isinstance(row["wayline_ids"], str) else row["wayline_ids"],
        "checklist": json.loads(row["checklist"]) if isinstance(row["checklist"], str) else row["checklist"],
        "status": row["status"], "created_by": row["created_by"],
        "created_at": row["created_at"].isoformat() + "Z", "updated_at": row["updated_at"].isoformat() + "Z",
        "status_at": row["status_at"].isoformat() + "Z" if row["status_at"] else None,
    }


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter(prefix="/api/mapping")
    current_user = user_dependency(settings)

    def load(cur, user: User, mid: str) -> dict:
        cur.execute("SELECT * FROM mission WHERE id = %s AND workspace_id = %s FOR UPDATE", (mid, user.workspace_id))
        row = cur.fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Einsatz nicht gefunden")
        return row

    def validate_refs(user: User, body: MissionIn) -> None:
        """Drone, pilot and routes must belong to the workspace."""
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                if body.drone_sn:
                    cur.execute("SELECT 1 FROM manage_device WHERE device_sn = %s AND workspace_id = %s AND domain = 0",
                                (body.drone_sn, user.workspace_id))
                    if not cur.fetchone():
                        raise HTTPException(status_code=422, detail="Drohne nicht im Arbeitsbereich")
                if body.pilot:
                    cur.execute("SELECT 1 FROM manage_user WHERE username = %s AND workspace_id = %s",
                                (body.pilot, user.workspace_id))
                    if not cur.fetchone():
                        raise HTTPException(status_code=422, detail="Pilot nicht im Arbeitsbereich")
                if body.wayline_ids:
                    marks = ",".join(["%s"] * len(body.wayline_ids))
                    cur.execute(f"SELECT wayline_id FROM wayline_file WHERE workspace_id = %s AND wayline_id IN ({marks})",
                                [user.workspace_id, *body.wayline_ids])
                    if len(cur.fetchall()) != len(set(body.wayline_ids)):
                        raise HTTPException(status_code=422, detail="Route nicht gefunden")
        finally:
            conn.close()

    @router.get("/missions/options")
    def options(user: User = Depends(current_user)) -> dict:
        """Choices for the mission form: purposes, drones, pilots, routes of the workspace."""
        conn = db.connect(settings, settings.media_database)
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT device_sn, nickname, device_name FROM manage_device WHERE workspace_id = %s"
                            " AND domain = 0 AND bound_status = 1 ORDER BY nickname", (user.workspace_id,))
                drones = [{"sn": r["device_sn"], "name": r["nickname"] or r["device_name"]} for r in cur.fetchall()]
                cur.execute("SELECT username, user_type FROM manage_user WHERE workspace_id = %s ORDER BY username",
                            (user.workspace_id,))
                pilots = [r["username"] for r in cur.fetchall()]
                cur.execute("SELECT wayline_id, name FROM wayline_file WHERE workspace_id = %s ORDER BY update_time DESC",
                            (user.workspace_id,))
                routes = [{"id": r["wayline_id"], "name": r["name"]} for r in cur.fetchall()]
        finally:
            conn.close()
        return {"purposes": PURPOSES, "statuses": list(STATUSES), "drones": drones, "pilots": pilots, "routes": routes,
                "checklists": {p: new_checklist(p) for p in PURPOSES}}

    @router.get("/missions")
    def list_missions(user: User = Depends(current_user), start: datetime | None = Query(None),
                      end: datetime | None = Query(None), status: str | None = Query(None),
                      mine: bool = Query(False)) -> list[dict]:
        sql, args = "SELECT * FROM mission WHERE workspace_id = %s", [user.workspace_id]
        if start:
            sql += " AND planned_start >= %s"
            args.append(_utc(start))
        if end:
            sql += " AND planned_start < %s"
            args.append(_utc(end))
        if status:
            sql += " AND status = %s"
            args.append(status)
        if mine:                                            # RC page: missions of this pilot or without pilot
            sql += " AND (pilot = %s OR pilot IS NULL)"
            args.append(user.username)
        with db.transaction(settings) as cur:
            cur.execute(sql + " ORDER BY planned_start", args)
            return [_out(r) for r in cur.fetchall()]

    @router.get("/missions/{mid}")
    def get_mission(mid: str, user: User = Depends(current_user)) -> dict:
        with db.transaction(settings) as cur:
            return _out(load(cur, user, mid))

    @router.post("/missions", status_code=201)
    def create(body: MissionIn, user: User = Depends(current_user)) -> dict:
        validate_refs(user, body)
        mid = str(uuid.uuid4())
        with db.transaction(settings) as cur:
            cur.execute("INSERT INTO mission (id, workspace_id, title, purpose, planned_start, duration_min, site, notes,"
                        " drone_sn, pilot, wayline_ids, checklist, status, created_by) VALUES"
                        " (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'planned', %s)",
                        (mid, user.workspace_id, body.title, body.purpose, _utc(body.planned_start), body.duration_min,
                         body.site, body.notes, body.drone_sn or None, body.pilot or None, json.dumps(body.wayline_ids),
                         json.dumps(new_checklist(body.purpose)), user.username))
            row = load(cur, user, mid)
        log.info("mission %s '%s' created by %s", mid[:8], body.title, user.username)
        return _out(row)

    @router.put("/missions/{mid}")
    def update(mid: str, body: MissionIn, user: User = Depends(current_user)) -> dict:
        validate_refs(user, body)
        with db.transaction(settings) as cur:
            row = load(cur, user, mid)
            old = json.loads(row["checklist"]) if isinstance(row["checklist"], str) else row["checklist"]
            checklist = merge_checklist(old, body.purpose)
            status = auto_status(row["status"], checklist)
            cur.execute("UPDATE mission SET title = %s, purpose = %s, planned_start = %s, duration_min = %s, site = %s,"
                        " notes = %s, drone_sn = %s, pilot = %s, wayline_ids = %s, checklist = %s, status = %s,"
                        " updated_at = UTC_TIMESTAMP(3) WHERE id = %s",
                        (body.title, body.purpose, _utc(body.planned_start), body.duration_min, body.site, body.notes,
                         body.drone_sn or None, body.pilot or None, json.dumps(body.wayline_ids), json.dumps(checklist),
                         status, mid))
            return _out(load(cur, user, mid))

    @router.put("/missions/{mid}/checklist/{key}")
    def set_check(mid: str, key: str, body: CheckIn, user: User = Depends(current_user)) -> dict:
        now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        with db.transaction(settings) as cur:
            row = load(cur, user, mid)
            if row["status"] in ("evaluated", "cancelled"):
                raise HTTPException(status_code=409, detail="Einsatz ist abgeschlossen")
            checklist = json.loads(row["checklist"]) if isinstance(row["checklist"], str) else row["checklist"]
            try:
                tick(checklist, key, body.done, user.username, now)
            except KeyError as exc:
                raise HTTPException(status_code=404, detail="Pr\u00fcfpunkt nicht gefunden") from exc
            status = auto_status(row["status"], checklist)
            # status_at only when the checklist moved the status (old/new compared as parameters: MySQL applies the
            # SET assignments left to right, a column comparison would already see the new status)
            cur.execute("UPDATE mission SET checklist = %s, status = %s, updated_at = UTC_TIMESTAMP(3),"
                        " status_at = IF(%s = %s, status_at, UTC_TIMESTAMP(3)) WHERE id = %s",
                        (json.dumps(checklist), status, row["status"], status, mid))
            return _out(load(cur, user, mid))

    @router.put("/missions/{mid}/status")
    def set_status(mid: str, body: StatusIn, user: User = Depends(current_user)) -> dict:
        with db.transaction(settings) as cur:
            row = load(cur, user, mid)
            try:
                check_transition(row["status"], body.status)
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            cur.execute("UPDATE mission SET status = %s, status_at = UTC_TIMESTAMP(3), updated_at = UTC_TIMESTAMP(3)"
                        " WHERE id = %s", (body.status, mid))
            log.info("mission %s: %s -> %s by %s", mid[:8], row["status"], body.status, user.username)
            return _out(load(cur, user, mid))

    @router.delete("/missions/{mid}")
    def delete(mid: str, user: User = Depends(current_user)) -> dict:
        with db.transaction(settings) as cur:
            load(cur, user, mid)
            cur.execute("DELETE FROM mission WHERE id = %s", (mid,))
        return {"deleted": mid}

    return router

