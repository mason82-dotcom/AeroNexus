"""Inspections: REST API, background processing and exports.

Storage (bucket PV_BUCKET through GDAL /vsis3/):
  inspections/<id>/meta.json        name, parameters, status, progress, per-image summary (workspace scoped)
  inspections/<id>/anomalies.json   merged anomalies with position, Delta-T, class, status (open/confirmed/dismissed)
  inspections/<id>/crops/<aid>.png  image crop of each anomaly
Images are processed one after another by a single worker thread (the Pi has 4 cores; GDAL/numpy use them).
Queued/running inspections are picked up again after a restart.
"""
import base64
import csv
import html
import io
import json
import logging
import queue
import re
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid

from . import analysis, detect, pdf, storage
from .server import HttpError, route, settings
from .thermal import ThermalError, ThermalParams, Tsdk

log = logging.getLogger("pv.inspections")
MAX_IMAGES = 2000
STATUSES = ("open", "confirmed", "dismissed")
CLASS_TEXT = {1: "beobachten", 2: "Wartung planen", 3: "dringend"}
_jobs: "queue.Queue[str]" = queue.Queue()
_lock = threading.Lock()                 # meta.json / anomalies.json read-modify-write
tsdk = Tsdk(settings.tsdk_url)


def converter(jpeg: bytes, tp: ThermalParams, distance: float):
    return tsdk.temperatures(jpeg, tp, distance)


# ------------------------------------------------------------------ storage

def _prefix() -> str:
    return f"/vsis3/{settings.bucket}/inspections"


def _key(iid: str, name: str) -> str:
    return f"{_prefix()}/{iid}/{name}"


def _load(iid: str, name: str):
    try:
        return json.loads(storage.read(_key(iid, name)))
    except storage.NotFound as exc:
        raise HttpError(404, "Inspektion nicht gefunden") from exc


def _save(iid: str, name: str, obj) -> None:
    storage.write(_key(iid, name), json.dumps(obj).encode())


def _meta(iid: str, user: dict | None) -> dict:
    meta = _load(iid, "meta.json")
    if user is not None and meta.get("workspace_id") != user["workspace_id"]:
        raise HttpError(404, "Inspektion nicht gefunden")
    return meta


def _public(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if k not in ("workspace_id", "files")}


def _update(iid: str, **fields) -> dict:
    with _lock:
        meta = _load(iid, "meta.json")
        meta.update(fields)
        _save(iid, "meta.json", meta)
        return meta


# ------------------------------------------------------------------ media lookup

_DJI_NAME = re.compile(r"^(DJI_)(\d{14})(_\d{4})_T(\.jpe?g)$", re.I)


def visible_sibling(thermal_name: str, by_name: dict[str, dict]) -> dict | None:
    """Visible photo taken with a thermal photo: same index, suffix _V/_W/_Z, time stamp up to 2 s later/earlier
    (Pilot 2 often stamps the visible photo one second after the thermal one)."""
    m = _DJI_NAME.match(thermal_name)
    if not m:
        return None
    prefix, stamp, index, ext = m.groups()
    t0 = time.mktime(time.strptime(stamp, "%Y%m%d%H%M%S"))
    for dt in (0, 1, -1, 2, -2):
        ts = time.strftime("%Y%m%d%H%M%S", time.localtime(t0 + dt))
        for suffix in ("V", "W", "Z"):
            hit = by_name.get(f"{prefix}{ts}{index}_{suffix}{ext}".upper())
            if hit:
                return hit
    return None


def media_files(token: str, file_ids: list[str]) -> list[dict]:
    """Selected images with object keys (and their visible sibling photos), via the mapping service with the
    caller's token (workspace scope)."""
    wanted, found, by_name, page = set(file_ids), {}, {}, 1
    while True:
        req = urllib.request.Request(f"{settings.mapping_url}/api/mapping/media?page={page}&page_size=500",
                                     headers={"x-auth-token": token})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                body = json.loads(resp.read())
        except urllib.error.URLError as exc:
            raise HttpError(502, f"Mapping-Dienst nicht erreichbar: {exc}") from exc
        for row in body["list"]:
            item = {k: row[k] for k in ("file_id", "file_name", "object_key")}
            by_name[row["file_name"].upper()] = item
            if row["file_id"] in wanted:
                found[row["file_id"]] = item
        if page * body["page_size"] >= body["total"]:
            break
        page += 1
    missing = wanted - found.keys()
    if missing:
        raise HttpError(422, f"{len(missing)} Bild(er) nicht gefunden")
    return [dict(found[f], visible=visible_sibling(found[f]["file_name"], by_name)) for f in file_ids]


def _media_path(file: dict) -> str:
    return f"/vsis3/{settings.media_bucket}/{file['object_key']}"


# ------------------------------------------------------------------ worker

def process(iid: str) -> None:
    meta = _load(iid, "meta.json")
    tp = ThermalParams.from_dict(meta["params"]["thermal"])
    dp = detect.Params.from_dict(meta["params"]["detect"])
    files = meta["files"]
    _update(iid, status="running", started_at=_now(), progress=0, error=None)
    images, found = [], []
    for n, file in enumerate(files, 1):
        try:
            jpeg = storage.read(_media_path(file))
            visible = None
            if file.get("visible"):
                try:
                    visible = storage.read(_media_path(file["visible"]))
                except storage.NotFound:
                    visible = None
            info, anomalies = analysis.analyze_image(jpeg, file, converter, tp, dp, visible)
            found.extend(anomalies)
        except ThermalError as exc:                       # SDK missing/failing: stop, all images would fail
            _update(iid, status="failed", error=str(exc), finished_at=_now())
            return
        except Exception as exc:                          # one broken image must not stop the inspection
            log.warning("inspection %s: %s failed: %s", iid[:8], file["file_name"], exc)
            info = {"file_id": file["file_id"], "file_name": file["file_name"], "anomalies": 0,
                    "skipped": f"Fehler: {exc}"}
        images.append(info)
        if n % 5 == 0 or n == len(files):
            try:
                _update(iid, progress=round(100 * n / len(files)))
            except HttpError:                             # deleted while running
                return
    try:
        _load(iid, "meta.json")
    except HttpError:                                     # deleted while running: leave no files behind
        log.info("inspection %s was deleted during processing", iid[:8])
        return
    merged = analysis.merge(found, float(meta["params"].get("merge_m", 1.0)))
    for a in merged:
        crop, crop_rgb = a.pop("crop", None), a.pop("crop_rgb", None)
        if crop:
            storage.write(_key(iid, f"crops/{a['id']}.png"), crop)
        if crop_rgb:
            storage.write(_key(iid, f"crops/{a['id']}_rgb.png"), crop_rgb)
        a["has_crop"], a["has_crop_rgb"] = bool(crop), bool(crop_rgb)
    for a in found:                                       # merged-away duplicates: free their crop bytes
        a.pop("crop", None)
        a.pop("crop_rgb", None)
    _save(iid, "anomalies.json", merged)
    counts = {str(k): sum(1 for a in merged if a["klass"] == k) for k in (1, 2, 3)}
    _update(iid, status="done", finished_at=_now(), progress=100, images=images,
            summary={"images": len(files), "analysed": sum(1 for i in images if "skipped" not in i),
                     "anomalies": len(merged), "by_class": counts,
                     "without_position": sum(1 for a in merged if a["lat"] is None)})
    log.info("inspection %s done: %d images, %d anomalies", iid[:8], len(files), len(merged))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _worker() -> None:
    while True:
        iid = _jobs.get()
        try:
            process(iid)
        except Exception:
            log.error("inspection %s crashed:\n%s", iid, traceback.format_exc())
            try:
                _update(iid, status="failed", error="interner Fehler, siehe Dienst-Log", finished_at=_now())
            except Exception:
                pass


def start_worker() -> None:
    threading.Thread(target=_worker, name="pv-worker", daemon=True).start()
    for iid in storage.list_dirs(_prefix() + "/"):          # resume after a restart
        try:
            if _load(iid, "meta.json").get("status") in ("queued", "running"):
                _jobs.put(iid)
        except HttpError:
            continue


# ------------------------------------------------------------------ API

@route("GET", "/api/pv/status")
def status(req, user):
    h = tsdk.health()
    return {"tsdk": bool(h.get("sdk")), "tsdk_runner": h.get("runner"), "tsdk_version": h.get("version"),
            "queue": _jobs.qsize()}


@route("POST", "/api/pv/inspections")
def create(req, user):
    body = req.json()
    file_ids = [str(f) for f in (body.get("file_ids") or [])]
    if not file_ids or len(file_ids) > MAX_IMAGES:
        raise HttpError(422, f"1 bis {MAX_IMAGES} W\u00e4rmebilder ausw\u00e4hlen")
    try:
        tp = ThermalParams.from_dict(body.get("thermal") or {})
        dp = detect.Params.from_dict(body.get("detect") or {})
        merge_m = float(body.get("merge_m", 1.0))
    except (TypeError, ValueError) as exc:
        raise HttpError(422, str(exc)) from exc
    if not (dp.min_delta >= 1 and dp.class1 <= dp.class2 <= dp.class3 and 0 <= merge_m <= 20):
        raise HttpError(422, "Schwellen: min. Delta-T >= 1 K, Klasse 1 <= 2 <= 3, Zusammenfassen 0-20 m")
    files = media_files(req.token, file_ids)
    iid = str(uuid.uuid4())
    name = str(body.get("name", "")).strip()[:80] or f"PV-Inspektion {time.strftime('%Y-%m-%d %H:%M')}"
    meta = {"id": iid, "workspace_id": user["workspace_id"], "created_by": user.get("username"),
            "created_at": _now(), "name": name, "status": "queued", "progress": 0,
            "params": analysis.params_dict(tp, dp, merge_m), "files": files, "image_count": len(files)}
    _save(iid, "meta.json", meta)
    _jobs.put(iid)
    log.info("inspection %s queued by %s: %d images", iid[:8], user.get("username"), len(files))
    return 201, _public(meta)


@route("GET", "/api/pv/inspections")
def list_inspections(req, user):
    out = []
    for iid in storage.list_dirs(_prefix() + "/"):
        try:
            out.append(_public(_meta(iid, user)))
        except HttpError:
            continue
    return sorted(out, key=lambda m: m["created_at"], reverse=True)


def _anomalies(iid: str) -> list[dict]:
    try:
        return _load(iid, "anomalies.json")
    except HttpError:
        return []


@route("GET", "/api/pv/inspections/{iid}")
def get_inspection(req, user, iid):
    meta = _meta(iid, user)
    return {**_public(meta), "anomalies": _anomalies(iid)}


@route("GET", "/api/pv/inspections/{iid}/crops/{aid}.png")
def crop(req, user, iid, aid):
    _meta(iid, user)
    try:
        return 200, storage.read(_key(iid, f"crops/{aid}.png")), "image/png"
    except storage.NotFound as exc:
        raise HttpError(404, "Kein Bildausschnitt") from exc


@route("PUT", "/api/pv/inspections/{iid}/anomalies/{aid}")
def set_status(req, user, iid, aid):
    _meta(iid, user)
    body = req.json()
    status_ = body.get("status")
    if status_ is not None and status_ not in STATUSES:
        raise HttpError(422, f"Status muss einer von {list(STATUSES)} sein")
    with _lock:
        anomalies = _anomalies(iid)
        a = next((x for x in anomalies if x["id"] == aid), None)
        if a is None:
            raise HttpError(404, "Anomalie nicht gefunden")
        if status_ is not None:
            a["status"] = status_
        if "note" in body:
            a["note"] = str(body["note"])[:500]
        _save(iid, "anomalies.json", anomalies)
    return a


@route("DELETE", "/api/pv/inspections/{iid}")
def delete(req, user, iid):
    _meta(iid, user)
    storage.remove_tree(f"{_prefix()}/{iid}")
    log.info("inspection %s deleted by %s", iid[:8], user.get("username"))
    return {"deleted": iid}


# ------------------------------------------------------------------ exports

def _reported(iid: str, include_dismissed: bool) -> list[dict]:
    return [a for a in _anomalies(iid) if include_dismissed or a["status"] != "dismissed"]


FIELDS = ["number", "klass", "status", "type", "delta", "t_max", "t_background", "area_m2", "lat", "lon",
          "height_source", "file_name", "seen_in", "note"]


def export_geojson(meta: dict, items: list[dict]) -> tuple[bytes, str, str]:
    feats = [{"type": "Feature", "geometry": {"type": "Point", "coordinates": [a["lon"], a["lat"]]},
              "properties": {k: (", ".join(a[k]) if k == "seen_in" else a.get(k)) for k in FIELDS}}
             for a in items if a["lat"] is not None]
    return json.dumps({"type": "FeatureCollection", "features": feats}).encode(), "application/geo+json", "geojson"


def export_csv(meta: dict, items: list[dict]) -> tuple[bytes, str, str]:
    buf = io.StringIO()
    w = csv.writer(buf, delimiter=";")
    w.writerow(FIELDS)
    for a in items:
        w.writerow([", ".join(a[k]) if k == "seen_in" else ("" if a.get(k) is None else a.get(k)) for k in FIELDS])
    return buf.getvalue().encode("utf-8-sig"), "text/csv; charset=utf-8", "csv"   # BOM: Excel opens UTF-8


def export_report(meta: dict, items: list[dict]) -> tuple[bytes, str, str]:
    """Self-contained HTML report (crops embedded), printable to PDF from the browser."""
    e = html.escape
    p = meta["params"]
    rows = []
    for a in items:
        img = ""
        if a.get("has_crop"):
            try:
                png = storage.read(_key(meta["id"], f"crops/{a['id']}.png"))
                img = f'<img src="data:image/png;base64,{base64.b64encode(png).decode()}">'
            except storage.NotFound:
                pass
        pos = f'{a["lat"]:.7f}, {a["lon"]:.7f}' if a["lat"] is not None else "ohne Position"
        area = "" if a["area_m2"] is None else f'{a["area_m2"]:.2f} m&sup2;'
        rows.append(
            f'<tr class="k{a["klass"]}"><td>{a["number"]}</td><td>{img}</td><td><b>Klasse {a["klass"]}</b><br>'
            f'{CLASS_TEXT.get(a["klass"], "")}</td><td>&Delta;T {a["delta"]:.1f} K<br>T<sub>max</sub> '
            f'{a["t_max"]:.1f} &deg;C<br>Umgebung {a["t_background"]:.1f} &deg;C</td><td>{e(a["type"])}<br>'
            f'{area}</td>'
            f'<td>{pos}<br><small>{e(", ".join(a["seen_in"]))}</small></td><td>{e(a["status"])}'
            f'{"<br>" + e(a["note"]) if a["note"] else ""}</td></tr>')
    s = meta.get("summary", {})
    t = p["thermal"]
    d = p["detect"]
    doc = f"""<!doctype html><html lang="de"><head><meta charset="utf-8"><title>{e(meta["name"])}</title>
<style>body{{font:13px sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #ccc;padding:4px;vertical-align:top}}img{{max-width:220px}}
.k3 td:nth-child(3){{background:#fdd}}.k2 td:nth-child(3){{background:#fec}}.k1 td:nth-child(3){{background:#ffd}}
@media print{{tr{{page-break-inside:avoid}}}}</style></head><body>
<h1>{e(meta["name"])}</h1>
<p>Erstellt {e(meta["created_at"])} von {e(meta.get("created_by") or "")}, ausgewertet {e(meta.get("finished_at") or "")}.<br>
Bilder: {s.get("images", 0)} (ausgewertet {s.get("analysed", 0)}), Anomalien: {len(items)}
(Klasse 3: {sum(1 for a in items if a["klass"] == 3)}, Klasse 2: {sum(1 for a in items if a["klass"] == 2)},
Klasse 1: {sum(1 for a in items if a["klass"] == 1)}).</p>
<p><small>Temperaturen: DJI Thermal SDK, Emissionsgrad {t["emissivity"]}, reflektierte Temperatur {t["reflected_temp"]} &deg;C,
Luftfeuchte {t["humidity"]} %. Erkennung: &Delta;T ab {d["min_delta"]} K zur Umgebung (4-px-Ring), Klassen ab
{d["class1"]} / {d["class2"]} / {d["class3"]} K (Praxis nach IEC TS 62446-3). Fehlerart und Fl&auml;che sind Sch&auml;tzungen aus
Bildgeometrie; Positionen aus GPS, Gimbal und Laser-Entfernungsmesser (Genauigkeit wenige Meter). Aussagekr&auml;ftig nur bei
Einstrahlung &gt; 600 W/m&sup2; und klarem Himmel.</small></p>
<table><tr><th>Nr.</th><th>Bild</th><th>Klasse</th><th>Temperatur</th><th>Art / Fl&auml;che</th><th>Position / Bilder</th>
<th>Status</th></tr>{"".join(rows)}</table></body></html>"""
    return doc.encode("utf-8"), "text/html; charset=utf-8", "html"


CLASS_RGB = {1: (0.98, 0.86, 0.08), 2: (0.98, 0.55, 0.09), 3: (0.96, 0.13, 0.18)}


def export_pdf(meta: dict, items: list[dict]) -> tuple[bytes, str, str]:
    """A4 report: summary page, then four anomalies per page with thermal and visible crop."""
    doc = pdf.Document(meta["name"])
    doc.new_page()
    s = meta.get("summary", {})
    t, d = meta["params"]["thermal"], meta["params"]["detect"]
    doc.line_text("PV-Inspektion (Thermografie)", 10, rgb=(0.4, 0.4, 0.4), gap=14)
    doc.line_text(meta["name"], 18, bold=True, gap=10)
    doc.line_text(f"Erstellt {meta['created_at'].replace('T', ' ').replace('Z', ' UTC')} von "
                  f"{meta.get('created_by') or ''}, ausgewertet {(meta.get('finished_at') or '').replace('T', ' ').replace('Z', ' UTC')}", 10)
    doc.line_text(f"Bilder: {s.get('images', 0)} (ausgewertet {s.get('analysed', 0)}), gemeldete Anomalien: {len(items)}",
                  10, gap=12)
    for k in (3, 2, 1):
        n = sum(1 for a in items if a["klass"] == k)
        doc.rect(50, doc.y - 2, 10, 10, CLASS_RGB[k])
        doc.line_text(f"Klasse {k} ({CLASS_TEXT[k]}): {n}", 11, x=66)
    doc.y -= 8
    doc.line_text("Messbedingungen und Verfahren", 11, bold=True)
    doc.wrap(f"Temperaturen: DJI Thermal SDK, Emissionsgrad {t['emissivity']}, reflektierte Temperatur "
             f"{t['reflected_temp']} \u00b0C, Luftfeuchte {t['humidity']} %. Erkennung: Delta-T ab {d['min_delta']} K "
             f"gegen\u00fcber der Modultemperatur rundum; Klassen ab {d['class1']} / {d['class2']} / {d['class3']} K "
             "(gebr\u00e4uchliche Praxis zu IEC TS 62446-3). Fehlerart und Fl\u00e4che sind Sch\u00e4tzungen aus der "
             "Bildgeometrie. Positionen aus GPS, Gimbalwinkeln und H\u00f6he bzw. Laser-Entfernungsmesser "
             "(Genauigkeit wenige Meter). Das Normalbild ist ein Ausschnitt des gleichzeitig aufgenommenen Fotos; "
             "der Rahmen ist dort eine N\u00e4herung. Aussagekr\u00e4ftig nur bei Einstrahlung \u00fcber 600 W/m\u00b2, "
             "klarem Himmel und Anlage unter Last.")
    missing = pdf.grey_placeholder()
    per_page, block = 4, 180.0
    for n, a in enumerate(items):
        if n % per_page == 0:
            doc.new_page()
            doc.line_text(f"{meta['name']} - Anomalien", 10, rgb=(0.4, 0.4, 0.4), gap=8)
        top = doc.y
        doc.rect(50, top - block + 14, 4, block - 14, CLASS_RGB[a["klass"]])
        for i, (key, suffix) in enumerate((("has_crop", ""), ("has_crop_rgb", "_rgb"))):
            img = None
            if a.get(key):
                try:
                    img = pdf.png_to_jpeg(storage.read(_key(meta["id"], f"crops/{a['id']}{suffix}.png")))
                except storage.NotFound:
                    img = None
            bx = 60 + i * 155
            doc.image(*(img or missing), bx, top - 155, 150, 150)
            if img is None:
                doc.text(bx + 30, top - 82, "kein Normalbild" if i else "kein Bildausschnitt", 9,
                         rgb=(0.4, 0.4, 0.4))
        x, y = 375, top - 4
        lines = [(f"Nr. {a['number']}  Klasse {a['klass']}", 12, True),
                 (CLASS_TEXT.get(a["klass"], ""), 9, False),
                 (f"Delta-T {a['delta']:.1f} K", 10, False),
                 (f"T max {a['t_max']:.1f} \u00b0C, Umgebung {a['t_background']:.1f} \u00b0C", 9, False),
                 (a["type"] + ("" if a["area_m2"] is None else f", {a['area_m2']:.2f} m\u00b2"), 9, False),
                 (f"{a['lat']:.7f}, {a['lon']:.7f}" if a["lat"] is not None else "ohne Position (kein GPS)", 9, False),
                 (f"Bilder: {', '.join(a['seen_in'])[:60]}", 8, False),
                 (f"Status: {STATUS_TEXT.get(a['status'], a['status'])}", 9, False)]
        for text, size, bold in lines:
            doc.text(x, y, text, size, bold)
            y -= size + 4
        if a.get("note"):
            note = a["note"]
            while note and y > top - 170:
                doc.text(x, y, note[:40], 8)
                note, y = note[40:], y - 11
        doc.y = top - block
    return doc.save(), "application/pdf", "pdf"


STATUS_TEXT = {"open": "offen", "confirmed": "best\u00e4tigt", "dismissed": "verworfen"}
EXPORTS = {"geojson": export_geojson, "csv": export_csv, "report": export_report, "pdf": export_pdf}


@route("GET", "/api/pv/inspections/{iid}/export/{fmt}")
def export(req, user, iid, fmt):
    if fmt not in EXPORTS:
        raise HttpError(404, f"Format muss eines von {list(EXPORTS)} sein")
    meta = _meta(iid, user)
    if meta["status"] != "done":
        raise HttpError(409, "Inspektion ist noch nicht fertig")
    items = _reported(iid, req.query.get("dismissed") == "1")
    data, ctype, ext = EXPORTS[fmt](meta, items)
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in meta["name"]).strip("_")[:60] or "pv"
    disposition = "inline" if fmt == "report" else "attachment"
    return 200, data, ctype, {"Content-Disposition": f'{disposition}; filename="{safe}.{ext}"'}
