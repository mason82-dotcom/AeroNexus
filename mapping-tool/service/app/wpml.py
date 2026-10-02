"""Read and edit DJI WPML route files (KMZ = wpmz/template.kml + wpmz/waylines.wpml).

Only mapping2d templates are edited (polygon + a few parameters). The edited copy contains
template.kml only: Pilot 2 computes the executable route from the template (DJI recommendation,
see docs/AeroNexus_Mapping_Studie.md). Unknown elements are kept untouched.
"""
import io
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass

from defusedxml.ElementTree import fromstring as safe_fromstring

KML_NS = "http://www.opengis.net/kml/2.2"
TEMPLATE = "wpmz/template.kml"
WAYLINES = "wpmz/waylines.wpml"
EDITABLE_TYPES = {"mapping2d"}
MAX_KMZ_BYTES = 20 * 1024 * 1024


class WpmlError(ValueError):
    pass


@dataclass
class Route:
    template_type: str
    wpml_ns: str
    polygon: list[list[float]] | None   # [[lon, lat], ...] without closing point
    params: dict
    flight_path: list[list[float]]      # [[lon, lat, height], ...] from waylines.wpml


def _read_zip(kmz: bytes) -> dict[str, bytes]:
    if len(kmz) > MAX_KMZ_BYTES:
        raise WpmlError("KMZ too large")
    try:
        with zipfile.ZipFile(io.BytesIO(kmz)) as zf:
            out = {}
            for info in zf.infolist():
                if info.file_size > MAX_KMZ_BYTES:
                    raise WpmlError(f"{info.filename} too large")
                out[info.filename] = zf.read(info)
            return out
    except zipfile.BadZipFile as exc:
        raise WpmlError("not a KMZ (zip) file") from exc


def _wpml_ns(root: ET.Element) -> str:
    for el in root.iter():
        if el.tag.startswith("{http://www.dji.com/wpmz/"):
            return el.tag[1:].split("}")[0]
    raise WpmlError("no wpml namespace found")


def _find(root: ET.Element, ns: str, name: str) -> ET.Element | None:
    return root.find(f".//{{{ns}}}{name}")


def _num(root: ET.Element, ns: str, name: str) -> float | None:
    el = _find(root, ns, name)
    try:
        return float(el.text) if el is not None and el.text else None
    except ValueError:
        return None


def _polygon(root: ET.Element) -> tuple[ET.Element | None, list[list[float]] | None]:
    el = root.find(f".//{{{KML_NS}}}Polygon//{{{KML_NS}}}coordinates")
    if el is None or not el.text:
        return None, None
    points = []
    for token in el.text.split():
        lon, lat = token.split(",")[:2]
        points.append([float(lon), float(lat)])
    if len(points) > 1 and points[0] == points[-1]:
        points = points[:-1]
    return el, points


def parse(kmz: bytes) -> Route:
    files = _read_zip(kmz)
    if TEMPLATE not in files:
        raise WpmlError("wpmz/template.kml missing")
    root = safe_fromstring(files[TEMPLATE])
    ns = _wpml_ns(root)
    ttype_el = _find(root, ns, "templateType")
    template_type = (ttype_el.text or "").strip() if ttype_el is not None else ""
    _, polygon = _polygon(root)
    params = {
        "height": _num(root, ns, "globalShootHeight") or _num(root, ns, "height"),
        "direction": _num(root, ns, "direction"),
        "margin": _num(root, ns, "margin"),
        "overlap_h": _num(root, ns, "orthoCameraOverlapH"),
        "overlap_w": _num(root, ns, "orthoCameraOverlapW"),
        "speed": _num(root, ns, "autoFlightSpeed"),
        "height_mode": (_find(root, ns, "heightMode").text if _find(root, ns, "heightMode") is not None else None),
        "surface_follow": _num(root, ns, "surfaceFollowModeEnable") == 1,
    }
    path: list[list[float]] = []
    if WAYLINES in files:
        wroot = safe_fromstring(files[WAYLINES])
        for placemark in wroot.iter(f"{{{KML_NS}}}Placemark"):
            coords = placemark.find(f".//{{{KML_NS}}}Point/{{{KML_NS}}}coordinates")
            if coords is None or not coords.text:
                continue
            lon, lat = coords.text.strip().split(",")[:2]
            height = _num(placemark, ns, "executeHeight")
            path.append([float(lon), float(lat), height if height is not None else 0.0])
    return Route(template_type=template_type, wpml_ns=ns, polygon=polygon, params=params, flight_path=path)


def _set(root: ET.Element, ns: str, name: str, value: str) -> bool:
    found = False
    for el in root.iter(f"{{{ns}}}{name}"):
        el.text = value
        found = True
    return found


def _fmt(value: float, digits: int = 6) -> str:
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _set_if_changed(root: ET.Element, ns: str, name: str, value: float, digits: int = 6) -> None:
    """Only touch elements whose value really changes, so the copy stays as close to Pilot 2's file as possible."""
    old = _num(root, ns, name)
    if old is None or abs(old - value) >= 0.5 * 10 ** -digits:
        _set(root, ns, name, _fmt(value, digits))


def edit_copy(kmz: bytes, polygon: list[list[float]], params: dict) -> bytes:
    """Return a new KMZ with only template.kml, polygon and params replaced."""
    files = _read_zip(kmz)
    raw = files.get(TEMPLATE)
    if raw is None:
        raise WpmlError("wpmz/template.kml missing")
    root = safe_fromstring(raw)
    ns = _wpml_ns(root)
    ttype = _find(root, ns, "templateType")
    if ttype is None or (ttype.text or "").strip() not in EDITABLE_TYPES:
        raise WpmlError("only mapping2d routes can be edited")

    coords_el, _ = _polygon(root)
    if coords_el is None:
        raise WpmlError("template has no polygon")
    tokens = coords_el.text.split()
    closed = len(tokens) > 1 and tokens[0] == tokens[-1]   # keep Pilot 2's ring convention
    ring = polygon + [polygon[0]] if closed else polygon
    coords_el.text = "\n" + "\n".join(f"{_fmt(lon, 10)},{_fmt(lat, 10)},0" for lon, lat in ring) + "\n"

    old_height = _num(root, ns, "globalShootHeight") or _num(root, ns, "height")
    new_height = params["height"]
    if old_height is None or abs(old_height - new_height) > 1e-3:
        for name in ("globalShootHeight", "height", "surfaceRelativeHeight"):
            if _find(root, ns, name) is not None:
                _set(root, ns, name, _fmt(new_height))
        # ellipsoid height keeps its offset to the shooting height
        ellipsoid = _num(root, ns, "ellipsoidHeight")
        if ellipsoid is not None and old_height is not None:
            _set(root, ns, "ellipsoidHeight", _fmt(ellipsoid + new_height - old_height))
    _set_if_changed(root, ns, "direction", params["direction"], 0)
    _set_if_changed(root, ns, "margin", params["margin"], 0)
    for name, key in (("orthoCameraOverlapH", "overlap_h"), ("orthoCameraOverlapW", "overlap_w"),
                      ("orthoLidarOverlapH", "overlap_h"), ("orthoLidarOverlapW", "overlap_w")):
        _set_if_changed(root, ns, name, params[key], 0)
    _set_if_changed(root, ns, "autoFlightSpeed", params["speed"], 3)
    now = str(int(time.time() * 1000))
    _set(root, ns, "createTime", now)
    _set(root, ns, "updateTime", now)

    ET.register_namespace("", KML_NS)
    ET.register_namespace("wpml", ns)
    xml = ET.tostring(root, encoding="UTF-8", xml_declaration=True)
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(TEMPLATE, xml)
        # keep resources (e.g. wpmz/res/...) but never the old executable route
        for name, data in files.items():
            if name not in (TEMPLATE, WAYLINES) and not name.endswith("/"):
                zf.writestr(name, data)
    return out.getvalue()
