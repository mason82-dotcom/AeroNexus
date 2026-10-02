"""Zone maps (management zones) from a vegetation index layer, application rates and exports.

Storage (bucket 'farming', through GDAL /vsis3/):
  zonemaps/<id>/meta.json       parameters, class limits, zone stats, rates
  zonemaps/<id>/zones.geojson   zone polygons, EPSG:4326, properties zone/area_ha/idx_mean/rate
  zonemaps/<id>/classes.tif     class raster (EPSG:25832) used for the ISO-XML grid
  zonemaps/<id>/export/...      generated exports
"""
import io
import json
import logging
import os
import tempfile
import time
import urllib.error
import urllib.request
import uuid
import zipfile
from pathlib import Path

import numpy as np
from osgeo import gdal, ogr, osr

from . import isoxml
from .server import HttpError, route, settings

log = logging.getLogger("farming.zones")
gdal.UseExceptions()
ogr.UseExceptions()
NODATA = -9999.0
WORK_CRS = "EPSG:25832"
# red (low index) -> green (high index); index 0 = class 1
ZONE_COLORS = {3: ["#d73027", "#fee08b", "#1a9850"],
               4: ["#d73027", "#fc8d59", "#91cf60", "#1a9850"],
               5: ["#d73027", "#fc8d59", "#fee08b", "#91cf60", "#1a9850"]}
UNITS = {"kg/ha": ("0006", "Setpoint Mass Per Area Application Rate"),     # DDI, value in mg/m2 = kg/ha * 100
         "l/ha": ("0001", "Setpoint Volume Per Area Application Rate")}   # DDI, value in mm3/m2 = l/ha * 100


# ------------------------------------------------------------------ storage helpers

def _prefix() -> str:
    return f"/vsis3/{settings.bucket}/zonemaps"


def _key(zid: str, name: str) -> str:
    return f"{_prefix()}/{zid}/{name}"


def _write(path: str, data: bytes) -> None:
    f = gdal.VSIFOpenL(path, "wb")
    try:
        gdal.VSIFWriteL(data, 1, len(data), f)
    finally:
        gdal.VSIFCloseL(f)


def _read(path: str) -> bytes:
    try:
        f = gdal.VSIFOpenL(path, "rb")
    except RuntimeError as exc:
        raise HttpError(404, "Nicht gefunden") from exc
    if f is None:                       # missing object: GDAL returns NULL here instead of raising
        raise HttpError(404, "Nicht gefunden")
    try:
        gdal.VSIFSeekL(f, 0, 2)
        size = gdal.VSIFTellL(f)
        gdal.VSIFSeekL(f, 0, 0)
        return gdal.VSIFReadL(1, size, f)
    finally:
        gdal.VSIFCloseL(f)


def _meta(zid: str, workspace_id: str) -> dict:
    meta = json.loads(_read(_key(zid, "meta.json")))
    if meta.get("workspace_id") != workspace_id:
        raise HttpError(404, "Zonenkarte nicht gefunden")
    return meta


def _layer(user: dict, token: str, layer_id: str) -> dict:
    """Index layer from the mapping service, looked up with the caller's own token."""
    req = urllib.request.Request(f"{settings.mapping_url}/api/mapping/layers", headers={"x-auth-token": token})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            layers = json.loads(resp.read())
    except urllib.error.URLError as exc:
        raise HttpError(502, f"Mapping-Dienst nicht erreichbar: {exc}") from exc
    layer = next((l for l in layers if l["id"] == layer_id), None)
    if not layer or not layer.get("job_id"):
        raise HttpError(404, "Layer nicht gefunden")
    if layer.get("kind") in (None, "orthophoto"):
        raise HttpError(422, "Zonen brauchen einen Vegetationsindex-Layer (NDVI, NDRE, GNDVI ...)")
    return layer


# ------------------------------------------------------------------ zone computation

def _classify(values: np.ndarray, valid: np.ndarray, classes: int, method: str) -> tuple[np.ndarray, list[float]]:
    v = values[valid]
    if method == "quantile":
        limits = list(np.quantile(v, np.linspace(0, 1, classes + 1)))
    else:
        limits = list(np.linspace(float(v.min()), float(v.max()), classes + 1))
    out = np.zeros(values.shape, dtype=np.uint8)
    inner = limits[1:-1]
    out[valid] = (np.digitize(values[valid], inner) + 1).astype(np.uint8)    # 1..classes
    return out, [round(float(x), 4) for x in limits]


def _index_path(layer: dict) -> str:
    """Index COG written by the compute agent (mapping-results/<job>/<kind>.tif)."""
    return f"/vsis3/{settings.results_bucket}/{layer['job_id']}/{layer['kind']}.tif"


def compute(layer: dict, classes: int, method: str, cell_m: float, min_area_m2: float) -> tuple[dict, bytes, bytes]:
    src = _index_path(layer)
    ds = gdal.Warp("", src, format="MEM", dstSRS=WORK_CRS, xRes=cell_m, yRes=cell_m, resampleAlg="average",
                   srcNodata=NODATA, dstNodata=NODATA, targetAlignedPixels=True)
    values = ds.GetRasterBand(1).ReadAsArray().astype("float32")
    valid = values != NODATA
    if valid.sum() < classes * 4:
        raise HttpError(422, "Zu wenige g\u00fcltige Rasterzellen; bitte ein kleineres Raster w\u00e4hlen")
    cls, limits = _classify(values, valid, classes, method)

    mem = gdal.GetDriverByName("MEM").Create("", ds.RasterXSize, ds.RasterYSize, 1, gdal.GDT_Byte)
    mem.SetGeoTransform(ds.GetGeoTransform())
    mem.SetProjection(ds.GetProjection())
    band = mem.GetRasterBand(1)
    band.WriteArray(cls)
    band.SetNoDataValue(0)
    sieve_px = max(0, int(round(min_area_m2 / (cell_m * cell_m))))
    if sieve_px > 1:
        gdal.SieveFilter(band, None, band, sieve_px, 8)
    cls = band.ReadAsArray()

    # polygons (one feature per connected area) in EPSG:25832, then reprojected to WGS84
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(25832)
    vds = ogr.GetDriverByName("Memory").CreateDataSource("zones")
    lyr = vds.CreateLayer("zones", srs, ogr.wkbPolygon)
    lyr.CreateField(ogr.FieldDefn("zone", ogr.OFTInteger))
    gdal.Polygonize(band, band.GetMaskBand(), lyr, 0, [], callback=None)
    wgs = osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    to_wgs = osr.CoordinateTransformation(srs, wgs)

    cell_area = cell_m * cell_m
    zones = []
    for z in range(1, classes + 1):
        m = cls == z
        zones.append({"zone": z, "color": ZONE_COLORS[classes][z - 1],
                      "min": limits[z - 1], "max": limits[z],
                      "idx_mean": round(float(values[m & valid].mean()), 4) if (m & valid).any() else None,
                      "area_ha": round(float(m.sum()) * cell_area / 10000, 3), "rate": None})
    features = []
    for feat in lyr:
        geom = feat.GetGeometryRef().Clone()
        geom = geom.SimplifyPreserveTopology(cell_m * 0.25)
        area_ha = geom.GetArea() / 10000
        geom.Transform(to_wgs)
        z = feat.GetField("zone")
        features.append({"type": "Feature", "geometry": json.loads(geom.ExportToJson(["COORDINATE_PRECISION=7"])),
                         "properties": {"zone": z, "area_ha": round(area_ha, 4),
                                        "idx_mean": zones[z - 1]["idx_mean"], "color": zones[z - 1]["color"]}})

    # class raster for the ISO-XML grid
    tif = tempfile.NamedTemporaryFile(suffix=".tif", delete=False).name
    gdal.Translate(tif, mem, format="GTiff", creationOptions=["COMPRESS=DEFLATE"])
    raster = Path(tif).read_bytes()
    os.unlink(tif)
    gt = ds.GetGeoTransform()
    summary = {"zones": zones, "limits": limits, "valid_ha": round(float(valid.sum()) * cell_area / 10000, 3),
               "grid": {"cols": ds.RasterXSize, "rows": ds.RasterYSize, "origin": [gt[0], gt[3]]}}
    geojson = json.dumps({"type": "FeatureCollection", "features": features}).encode()
    return summary, geojson, raster


@route("POST", "/api/farming/zonemaps")
def create_zonemap(req, user):
    body = req.json()
    layer_id = str(body.get("layer_id", ""))
    classes = int(body.get("classes", 3))
    method = body.get("method", "quantile")
    cell_m = float(body.get("cell_m", 5))
    min_area = float(body.get("min_area_m2", 100))
    name = str(body.get("name", "")).strip()[:80]
    if classes not in (3, 4, 5) or method not in ("quantile", "equal") or not (1 <= cell_m <= 50) \
            or not (0 <= min_area <= 50000):
        raise HttpError(422, "Klassen 3-5, Methode quantile|equal, Raster 1-50 m, Mindestfl\u00e4che 0-50000 m2")
    layer = _layer(user, req.token, layer_id)
    t0 = time.time()
    summary, geojson, raster = compute(layer, classes, method, cell_m, min_area)
    zid = str(uuid.uuid4())
    meta = {"id": zid, "workspace_id": user["workspace_id"], "created_by": user.get("username"),
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "name": name or f"{layer['name']} - {classes} zones",
            "layer_id": layer_id, "layer_name": layer["name"], "index": layer["kind"], "job_id": layer["job_id"],
            "relative": bool(layer.get("relative")),
            "params": {"classes": classes, "method": method, "cell_m": cell_m, "min_area_m2": min_area},
            "product": "", "unit": "kg/ha", **summary}
    _write(_key(zid, "zones.geojson"), geojson)
    _write(_key(zid, "classes.tif"), raster)
    _write(_key(zid, "meta.json"), json.dumps(meta).encode())
    log.info("zone map %s from layer %s: %d classes, %.1fs", zid, layer_id, classes, time.time() - t0)
    return 201, _public(meta)


def _public(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if k != "workspace_id"}


@route("GET", "/api/farming/zonemaps")
def list_zonemaps(req, user):
    ids = [d.rstrip("/") for d in (gdal.ReadDir(_prefix() + "/") or [])]
    out = []
    for zid in ids:
        try:
            meta = _meta(zid, user["workspace_id"])
        except HttpError:
            continue
        if req.query.get("layer_id") and meta["layer_id"] != req.query["layer_id"]:
            continue
        out.append(_public(meta))
    return sorted(out, key=lambda m: m["created_at"], reverse=True)


@route("GET", "/api/farming/zonemaps/{zid}")
def get_zonemap(req, user, zid):
    return _public(_meta(zid, user["workspace_id"]))


@route("GET", "/api/farming/zonemaps/{zid}/zones.geojson")
def get_geojson(req, user, zid):
    _meta(zid, user["workspace_id"])
    return 200, _read(_key(zid, "zones.geojson")), "application/geo+json"


@route("DELETE", "/api/farming/zonemaps/{zid}")
def delete_zonemap(req, user, zid):
    _meta(zid, user["workspace_id"])
    gdal.RmdirRecursive(f"{_prefix()}/{zid}")
    log.info("zone map %s deleted by %s", zid, user.get("username"))
    return {"deleted": zid}


# ------------------------------------------------------------------ rates and exports

@route("PUT", "/api/farming/zonemaps/{zid}/rates")
def set_rates(req, user, zid):
    meta = _meta(zid, user["workspace_id"])
    body = req.json()
    unit = body.get("unit", "kg/ha")
    product = str(body.get("product", "")).strip()[:40]
    rates = body.get("rates") or {}
    if unit not in UNITS:
        raise HttpError(422, f"Einheit muss eine von {list(UNITS)} sein")
    for z in meta["zones"]:
        value = rates.get(str(z["zone"]), z["rate"])
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError) as exc:
                raise HttpError(422, f"Menge f\u00fcr Zone {z['zone']} ist keine Zahl") from exc
            if not 0 <= value <= 100000:
                raise HttpError(422, f"Menge f\u00fcr Zone {z['zone']} au\u00dferhalb 0-100000")
        z["rate"] = value
    meta.update(unit=unit, product=product, rates_updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    _write(_key(zid, "meta.json"), json.dumps(meta).encode())
    return _public(meta)


def _features_with_rates(meta: dict) -> dict:
    fc = json.loads(_read(_key(meta["id"], "zones.geojson")))
    by_zone = {z["zone"]: z for z in meta["zones"]}
    for f in fc["features"]:
        z = by_zone[f["properties"]["zone"]]
        f["properties"].update(rate=z["rate"], unit=meta["unit"], product=meta["product"], index=meta["index"])
    return fc


def _safe_name(text: str) -> str:
    keep = "".join(c if c.isalnum() or c in "-_" else "_" for c in text)
    return keep.strip("_")[:60] or "zones"


def export_geojson(meta: dict) -> tuple[bytes, str, str]:
    return (json.dumps(_features_with_rates(meta)).encode(), "application/geo+json",
            f"{_safe_name(meta['name'])}.geojson")


def export_shapefile(meta: dict) -> tuple[bytes, str, str]:
    fc = _features_with_rates(meta)
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "zones.geojson"
        src.write_text(json.dumps(fc))
        base = _safe_name(meta["name"])[:30]
        shp = Path(tmp) / f"{base}.shp"
        # short DBF field names, WGS84 as most terminals expect it
        sql = ("SELECT zone AS ZONE, rate AS RATE, unit AS UNIT, product AS PRODUCT, area_ha AS AREA_HA, "
               "idx_mean AS IDX_MEAN FROM zones")
        gdal.VectorTranslate(str(shp), str(src), format="ESRI Shapefile", SQLStatement=sql, dstSRS="EPSG:4326",
                             layerName=base, layerCreationOptions=["ENCODING=UTF-8"])
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for p in sorted(Path(tmp).glob(f"{base}.*")):
                zf.write(p, p.name)
        return buf.getvalue(), "application/zip", f"{base}_shapefile.zip"


def export_kml(meta: dict) -> tuple[bytes, str, str]:
    fc = _features_with_rates(meta)

    def kml_color(hex_rgb: str, alpha: str = "99") -> str:
        return alpha + hex_rgb[5:7] + hex_rgb[3:5] + hex_rgb[1:3]   # KML is aabbggrr

    def esc(text) -> str:
        return str(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")

    def ring(coords) -> str:
        return " ".join(f"{x:.7f},{y:.7f},0" for x, y in coords)

    styles = "".join(f'<Style id="z{z["zone"]}"><LineStyle><color>{kml_color(z["color"], "ff")}</color>'
                     f'<width>1</width></LineStyle><PolyStyle><color>{kml_color(z["color"])}</color></PolyStyle>'
                     f"</Style>" for z in meta["zones"])
    marks = []
    for f in fc["features"]:
        p, g = f["properties"], f["geometry"]
        polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"]
        geom = "".join(
            "<Polygon><outerBoundaryIs><LinearRing><coordinates>" + ring(poly[0]) + "</coordinates></LinearRing>"
            "</outerBoundaryIs>" + "".join("<innerBoundaryIs><LinearRing><coordinates>" + ring(h) +
                                           "</coordinates></LinearRing></innerBoundaryIs>" for h in poly[1:]) +
            "</Polygon>" for poly in polys)
        if len(polys) > 1:
            geom = f"<MultiGeometry>{geom}</MultiGeometry>"
        rate = "" if p["rate"] is None else f'{p["rate"]:g} {esc(p["unit"])}'
        marks.append(f'<Placemark><name>Zone {p["zone"]}</name><styleUrl>#z{p["zone"]}</styleUrl>'
                     f'<description>{esc(meta["index"].upper())} mean {p["idx_mean"]}, {p["area_ha"]} ha'
                     f'{", rate " + rate if rate else ""}</description><ExtendedData>'
                     f'<Data name="zone"><value>{p["zone"]}</value></Data>'
                     f'<Data name="rate"><value>{"" if p["rate"] is None else p["rate"]}</value></Data>'
                     f'<Data name="unit"><value>{esc(p["unit"])}</value></Data>'
                     f'<Data name="product"><value>{esc(p["product"])}</value></Data>'
                     f"</ExtendedData>{geom}</Placemark>")
    doc = (f'<?xml version="1.0" encoding="UTF-8"?><kml xmlns="http://www.opengis.net/kml/2.2"><Document>'
           f"<name>{esc(meta['name'])}</name>{styles}{''.join(marks)}</Document></kml>")
    return doc.encode(), "application/vnd.google-earth.kml+xml", f"{_safe_name(meta['name'])}.kml"


def export_isoxml(meta: dict) -> tuple[bytes, str, str]:
    if any(z["rate"] is None for z in meta["zones"]):
        raise HttpError(422, "ISO-XML braucht f\u00fcr jede Zone eine Menge")
    raster = _read(_key(meta["id"], "classes.tif"))
    data = isoxml.build(meta, raster, UNITS[meta["unit"]][0])
    return data, "application/zip", f"{_safe_name(meta['name'])}_TASKDATA.zip"


EXPORTS = {"geojson": export_geojson, "shapefile": export_shapefile, "kml": export_kml, "isoxml": export_isoxml}


@route("GET", "/api/farming/zonemaps/{zid}/export/{fmt}")
def export(req, user, zid, fmt):
    if fmt not in EXPORTS:
        raise HttpError(404, f"Format muss eines von {list(EXPORTS)} sein")
    meta = _meta(zid, user["workspace_id"])
    data, ctype, filename = EXPORTS[fmt](meta)
    log.info("zone map %s exported as %s by %s", zid, fmt, user.get("username"))
    return 200, data, ctype, {"Content-Disposition": f'attachment; filename="{filename}"'}
