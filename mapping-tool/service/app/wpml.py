"""Read and edit DJI WPML route files (KMZ = wpmz/template.kml + wpmz/waylines.wpml).

Only mapping2d templates are edited (polygon + a few parameters), as text, so everything else
stays byte-identical to what Pilot 2 wrote. See edit_copy for the Pilot 2 findings.
"""
import io
import re
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


def _fmt(value: float, digits: int = 6) -> str:
    return f"{value:.{digits}f}".rstrip("0").rstrip(".")


def _wpml_prefix(text: str, ns: str) -> str:
    m = re.search(r'xmlns:([A-Za-z_][\w.-]*)="' + re.escape(ns) + '"', text)
    if not m:
        raise WpmlError("wpml namespace prefix not found")
    return m.group(1)


def _replace_value(text: str, prefix: str, name: str, value: str) -> str:
    pattern = re.compile(rf"(<{prefix}:{name}>)([^<]*)(</{prefix}:{name}>)")
    return pattern.sub(lambda m: m.group(1) + value + m.group(3), text)


def _replace_if_changed(text: str, prefix: str, name: str, old: float | None, value: float, digits: int) -> str:
    if old is not None and abs(old - value) < 0.5 * 10 ** -digits:
        return text
    return _replace_value(text, prefix, name, _fmt(value, digits))


def edit_copy(kmz: bytes, polygon: list[list[float]], params: dict) -> bytes:
    """Return a new KMZ with polygon and params replaced in template.kml.

    Pilot 2 is picky about the file layout (a re-serialized template made it read the polygon as
    lat/lon swapped). So the template is edited as text: only the values change, declaration,
    indentation, element order and number style stay exactly as Pilot 2 wrote them.
    waylines.wpml is kept: Pilot 2 refuses a KMZ without it ("route file deleted") and recomputes
    the executable route from the template when it opens a mapping2d route.
    """
    files = _read_zip(kmz)
    raw = files.get(TEMPLATE)
    if raw is None:
        raise WpmlError("wpmz/template.kml missing")
    root = safe_fromstring(raw)
    ns = _wpml_ns(root)
    ttype = _find(root, ns, "templateType")
    if ttype is None or (ttype.text or "").strip() not in EDITABLE_TYPES:
        raise WpmlError("only mapping2d routes can be edited")
    text = raw.decode("utf-8")
    prefix = _wpml_prefix(text, ns)

    # polygon: the <coordinates> block inside <Polygon>
    poly_at = text.find("<Polygon>")
    start = text.find("<coordinates>", poly_at)
    end = text.find("</coordinates>", start)
    if poly_at < 0 or start < 0 or end < 0:
        raise WpmlError("template has no polygon")
    inner = text[start + len("<coordinates>"):end]
    tokens = inner.split()
    closed = len(tokens) > 1 and tokens[0] == tokens[-1]
    first = next((line for line in inner.splitlines() if line.strip()), "")
    indent = first[: len(first) - len(first.lstrip())]
    tail = inner[inner.rfind("\n"):] if "\n" in inner else ""
    alt = tokens[0].split(",")[2] if tokens and tokens[0].count(",") >= 2 else "0"
    ring = polygon + [polygon[0]] if closed else polygon
    lines = "\n".join(f"{indent}{lon:.15g},{lat:.15g},{alt}" for lon, lat in ring)
    text = text[: start + len("<coordinates>")] + "\n" + lines + tail + text[end:]

    old_height = _num(root, ns, "globalShootHeight") or _num(root, ns, "height")
    new_height = params["height"]
    if old_height is None or abs(old_height - new_height) > 1e-3:
        for name in ("globalShootHeight", "height", "surfaceRelativeHeight"):
            text = _replace_value(text, prefix, name, _fmt(new_height))
        ellipsoid = _num(root, ns, "ellipsoidHeight")
        if ellipsoid is not None and old_height is not None:
            text = _replace_value(text, prefix, "ellipsoidHeight", _fmt(ellipsoid + new_height - old_height))
    text = _replace_if_changed(text, prefix, "direction", _num(root, ns, "direction"), params["direction"], 0)
    text = _replace_if_changed(text, prefix, "margin", _num(root, ns, "margin"), params["margin"], 0)
    for name, key in (("orthoCameraOverlapH", "overlap_h"), ("orthoCameraOverlapW", "overlap_w"),
                      ("orthoLidarOverlapH", "overlap_h"), ("orthoLidarOverlapW", "overlap_w")):
        text = _replace_if_changed(text, prefix, name, _num(root, ns, name), params[key], 0)
    text = _replace_if_changed(text, prefix, "autoFlightSpeed", _num(root, ns, "autoFlightSpeed"), params["speed"], 3)
    now = str(int(time.time() * 1000))
    text = _replace_value(text, prefix, "createTime", now)
    text = _replace_value(text, prefix, "updateTime", now)

    # sanity check: the edited template parses and carries exactly the requested polygon
    check = parse_template(text.encode("utf-8"))
    if check is None or len(check) != len(polygon) or any(
            abs(a[0] - b[0]) > 1e-9 or abs(a[1] - b[1]) > 1e-9 for a, b in zip(check, polygon)):
        raise WpmlError("internal error: edited polygon does not round-trip")

    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(TEMPLATE, text.encode("utf-8"))
        for name, data in files.items():
            if name != TEMPLATE and not name.endswith("/"):
                zf.writestr(name, data)
    return out.getvalue()


def parse_template(raw: bytes) -> list[list[float]] | None:
    _, polygon = _polygon(safe_fromstring(raw))
    return polygon
