"""Read and edit DJI WPML route files (KMZ = wpmz/template.kml + wpmz/waylines.wpml).

Only mapping2d templates are edited (polygon + a few parameters), as text, so everything else
stays byte-identical to what Pilot 2 wrote. See edit_copy for the Pilot 2 findings.
"""
import io
import math
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
    image_format: str | None = None     # photo lenses ("visable", "ir", "visable,ir", ...)


def _read_zip(kmz: bytes) -> dict[str, bytes]:
    if len(kmz) > MAX_KMZ_BYTES:
        raise WpmlError("KMZ zu gro\u00df")
    try:
        with zipfile.ZipFile(io.BytesIO(kmz)) as zf:
            out = {}
            for info in zf.infolist():
                if info.file_size > MAX_KMZ_BYTES:
                    raise WpmlError(f"{info.filename} zu gro\u00df")
                out[info.filename] = zf.read(info)
            return out
    except zipfile.BadZipFile as exc:
        raise WpmlError("keine KMZ-Datei (ZIP)") from exc


def _wpml_ns(root: ET.Element) -> str:
    for el in root.iter():
        if el.tag.startswith("{http://www.dji.com/wpmz/"):
            return el.tag[1:].split("}")[0]
    raise WpmlError("kein WPML-Namespace gefunden")


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
        raise WpmlError("wpmz/template.kml fehlt")
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
    fmt = _find(root, ns, "imageFormat")
    return Route(template_type=template_type, wpml_ns=ns, polygon=polygon, params=params, flight_path=path,
                 image_format=(fmt.text or "").strip() if fmt is not None else None)


def _fmt(value: float, digits: int = 6) -> str:
    """75.000000 -> 75, 7.290 -> 7.29; whole numbers keep their zeros (70 stays 70, 90 stays 90)."""
    text = f"{value:.{digits}f}"
    return text.rstrip("0").rstrip(".") if "." in text else text


def _wpml_prefix(text: str, ns: str) -> str:
    m = re.search(r'xmlns:([A-Za-z_][\w.-]*)="' + re.escape(ns) + '"', text)
    if not m:
        raise WpmlError("WPML-Namespace-Pr\u00e4fix nicht gefunden")
    return m.group(1)


def _replace_value(text: str, prefix: str, name: str, value: str) -> str:
    pattern = re.compile(rf"(<{prefix}:{name}>)([^<]*)(</{prefix}:{name}>)")
    return pattern.sub(lambda m: m.group(1) + value + m.group(3), text)


def _replace_if_changed(text: str, prefix: str, name: str, old: float | None, value: float, digits: int) -> str:
    if old is not None and abs(old - value) < 0.5 * 10 ** -digits:
        return text
    return _replace_value(text, prefix, name, _fmt(value, digits))


# ---------------------------------------------------------------- executable path (waylines.wpml)
#
# Pilot 2 does NOT recompute a synced area route: it flies waylines.wpml as it is (verified 2026-10-03).
# So every copy gets a waylines.wpml generated from the planned lanes, built like Pilot's own nadir mapping
# routes (shootType time): header + start actions of the template's waylines.wpml, one placemark per lane end,
# gimbal -90 + startTimeLapse at the first point, stopTimeLapse at the last; constant height above take-off.
# Template switches that would change the path (terrain follow, smart oblique, elevation optimisation) are off.

MIN_SHOT_INTERVAL = 1.5          # s, timed shots faster than this are not reliable on M3/M4 cameras
PATH_OFF_SWITCHES = ("surfaceFollowModeEnable", "isRealtimeSurfaceFollow", "smartObliqueEnable",
                     "elevationOptimizeEnable")


def _bearing(a: list[float], b: list[float]) -> float:
    """-180..180 deg, clockwise from north (as Pilot writes waypointHeadingAngle)."""
    k = math.cos(math.radians(a[1]))
    return math.degrees(math.atan2((b[0] - a[0]) * k, b[1] - a[1]))


def _dist(a: list[float], b: list[float]) -> float:
    k = math.cos(math.radians(a[1]))
    return math.hypot((b[0] - a[0]) * 111320.0 * k, (b[1] - a[1]) * 111320.0)


GIMBAL_NADIR = """            <wpml:action>
              <wpml:actionId>0</wpml:actionId>
              <wpml:actionActuatorFunc>gimbalRotate</wpml:actionActuatorFunc>
              <wpml:actionActuatorFuncParam>
                <wpml:gimbalHeadingYawBase>aircraft</wpml:gimbalHeadingYawBase>
                <wpml:gimbalRotateMode>absoluteAngle</wpml:gimbalRotateMode>
                <wpml:gimbalPitchRotateEnable>1</wpml:gimbalPitchRotateEnable>
                <wpml:gimbalPitchRotateAngle>-90</wpml:gimbalPitchRotateAngle>
                <wpml:gimbalRollRotateEnable>0</wpml:gimbalRollRotateEnable>
                <wpml:gimbalRollRotateAngle>0</wpml:gimbalRollRotateAngle>
                <wpml:gimbalYawRotateEnable>0</wpml:gimbalYawRotateEnable>
                <wpml:gimbalYawRotateAngle>0</wpml:gimbalYawRotateAngle>
                <wpml:gimbalRotateTimeEnable>0</wpml:gimbalRotateTimeEnable>
                <wpml:gimbalRotateTime>10</wpml:gimbalRotateTime>
                <wpml:payloadPositionIndex>0</wpml:payloadPositionIndex>
              </wpml:actionActuatorFuncParam>
            </wpml:action>"""


def build_waylines(old_waylines: str, prefix: str, lanes: list, height: float, speed: float,
                   photo_spacing: float, lens_index: str) -> tuple[str, dict]:
    """waylines.wpml for the given lanes ([[lon, lat], [lon, lat]] in flying order). Returns (text, info)."""
    if not lanes:
        raise WpmlError("keine Flugbahnen in der Fl\u00e4che (Fl\u00e4che zu klein?)")
    if "<Folder>" not in old_waylines:
        raise WpmlError("waylines.wpml der Vorlage ohne Folder")
    head = old_waylines[:old_waylines.index("<Folder>")]
    m = re.search(r"[ \t]*<%s:startActionGroup>.*?</%s:startActionGroup>\n?" % (prefix, prefix), old_waylines, re.S)
    start_group = m.group(0) if m else ""
    points = [p for lane in lanes for p in lane]
    if speed * MIN_SHOT_INTERVAL > photo_spacing:
        speed = max(1.0, photo_spacing / MIN_SHOT_INTERVAL)       # keep the overlap: never shoot faster
    interval = photo_spacing / speed
    length = sum(_dist(points[i], points[i + 1]) for i in range(len(points) - 1))
    duration = length / speed + 3.0 * len(points)                 # stop at every lane end
    n = len(points)
    out = [head + "<Folder>",
           f"      <{prefix}:templateId>0</{prefix}:templateId>",
           f"      <{prefix}:executeHeightMode>relativeToStartPoint</{prefix}:executeHeightMode>",
           f"      <{prefix}:waylineId>0</{prefix}:waylineId>",
           f"      <{prefix}:distance>{length:.3f}</{prefix}:distance>",
           f"      <{prefix}:duration>{duration:.3f}</{prefix}:duration>",
           f"      <{prefix}:autoFlightSpeed>{_fmt(speed, 3)}</{prefix}:autoFlightSpeed>"]
    if start_group:
        out.append(start_group.rstrip("\n"))
    for i, p in enumerate(points):
        last = i == n - 1
        heading = 0.0 if last else _bearing(p, points[i + 1])
        pm = [f"      <Placemark>",
              f"        <Point>",
              f"          <coordinates>",
              f"            {p[0]:.15g},{p[1]:.15g}",
              f"          </coordinates>",
              f"        </Point>",
              f"        <{prefix}:index>{i}</{prefix}:index>",
              f"        <{prefix}:executeHeight>{_fmt(height, 3)}</{prefix}:executeHeight>",
              f"        <{prefix}:waypointSpeed>{_fmt(speed, 3)}</{prefix}:waypointSpeed>",
              f"        <{prefix}:waypointHeadingParam>",
              f"          <{prefix}:waypointHeadingMode>followWayline</{prefix}:waypointHeadingMode>",
              f"          <{prefix}:waypointHeadingAngle>{_fmt(heading, 3)}</{prefix}:waypointHeadingAngle>",
              f"          <{prefix}:waypointPoiPoint>0.000000,0.000000,0.000000</{prefix}:waypointPoiPoint>",
              f"          <{prefix}:waypointHeadingAngleEnable>{0 if last else 1}</{prefix}:waypointHeadingAngleEnable>",
              f"          <{prefix}:waypointHeadingPathMode>followBadArc</{prefix}:waypointHeadingPathMode>",
              f"          <{prefix}:waypointHeadingPoiIndex>0</{prefix}:waypointHeadingPoiIndex>",
              f"        </{prefix}:waypointHeadingParam>",
              f"        <{prefix}:waypointTurnParam>",
              f"          <{prefix}:waypointTurnMode>toPointAndStopWithDiscontinuityCurvature</{prefix}:waypointTurnMode>",
              f"          <{prefix}:waypointTurnDampingDist>0</{prefix}:waypointTurnDampingDist>",
              f"        </{prefix}:waypointTurnParam>",
              f"        <{prefix}:useStraightLine>1</{prefix}:useStraightLine>"]
        if i == 0:
            pm += [f"        <{prefix}:actionGroup>",
                   f"          <{prefix}:actionGroupId>0</{prefix}:actionGroupId>",
                   f"          <{prefix}:actionGroupStartIndex>0</{prefix}:actionGroupStartIndex>",
                   f"          <{prefix}:actionGroupEndIndex>{n - 1}</{prefix}:actionGroupEndIndex>",
                   f"          <{prefix}:actionGroupMode>sequence</{prefix}:actionGroupMode>",
                   f"          <{prefix}:actionTrigger>",
                   f"            <{prefix}:actionTriggerType>betweenAdjacentPoints</{prefix}:actionTriggerType>",
                   f"          </{prefix}:actionTrigger>",
                   GIMBAL_NADIR.replace("wpml:", f"{prefix}:"),
                   f"            <{prefix}:action>",
                   f"              <{prefix}:actionId>1</{prefix}:actionId>",
                   f"              <{prefix}:actionActuatorFunc>startTimeLapse</{prefix}:actionActuatorFunc>",
                   f"              <{prefix}:actionActuatorFuncParam>",
                   f"                <{prefix}:payloadPositionIndex>0</{prefix}:payloadPositionIndex>",
                   f"                <{prefix}:useGlobalPayloadLensIndex>0</{prefix}:useGlobalPayloadLensIndex>",
                   f"                <{prefix}:payloadLensIndex>{lens_index}</{prefix}:payloadLensIndex>",
                   f"                <{prefix}:minShootInterval>{interval:.3f}</{prefix}:minShootInterval>",
                   f"              </{prefix}:actionActuatorFuncParam>",
                   f"            </{prefix}:action>",
                   f"        </{prefix}:actionGroup>"]
        if last:
            pm += [f"        <{prefix}:actionGroup>",
                   f"          <{prefix}:actionGroupId>1</{prefix}:actionGroupId>",
                   f"          <{prefix}:actionGroupStartIndex>{i}</{prefix}:actionGroupStartIndex>",
                   f"          <{prefix}:actionGroupEndIndex>{i}</{prefix}:actionGroupEndIndex>",
                   f"          <{prefix}:actionGroupMode>sequence</{prefix}:actionGroupMode>",
                   f"          <{prefix}:actionTrigger>",
                   f"            <{prefix}:actionTriggerType>reachPoint</{prefix}:actionTriggerType>",
                   f"          </{prefix}:actionTrigger>",
                   f"          <{prefix}:action>",
                   f"            <{prefix}:actionId>0</{prefix}:actionId>",
                   f"            <{prefix}:actionActuatorFunc>stopTimeLapse</{prefix}:actionActuatorFunc>",
                   f"            <{prefix}:actionActuatorFuncParam>",
                   f"              <{prefix}:payloadPositionIndex>0</{prefix}:payloadPositionIndex>",
                   f"              <{prefix}:payloadLensIndex>{lens_index}</{prefix}:payloadLensIndex>",
                   f"            </{prefix}:actionActuatorFuncParam>",
                   f"          </{prefix}:action>",
                   f"        </{prefix}:actionGroup>"]
        pm += [f"        <{prefix}:waypointGimbalHeadingParam>",
               f"          <{prefix}:waypointGimbalPitchAngle>0</{prefix}:waypointGimbalPitchAngle>",
               f"          <{prefix}:waypointGimbalYawAngle>0</{prefix}:waypointGimbalYawAngle>",
               f"        </{prefix}:waypointGimbalHeadingParam>",
               f"        <{prefix}:isRisky>0</{prefix}:isRisky>",
               f"        <{prefix}:waypointWorkType>0</{prefix}:waypointWorkType>",
               f"      </Placemark>"]
        out += pm
    out += ["    </Folder>", "  </Document>", "</kml>", ""]
    info = {"waypoints": n, "lanes": len(lanes), "length_m": round(length), "duration_s": round(duration),
            "speed": round(speed, 2), "shot_interval_s": round(interval, 2)}
    return "\n".join(out), info


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
        raise WpmlError("wpmz/template.kml fehlt")
    root = safe_fromstring(raw)
    ns = _wpml_ns(root)
    ttype = _find(root, ns, "templateType")
    if ttype is None or (ttype.text or "").strip() not in EDITABLE_TYPES:
        raise WpmlError("nur Fl\u00e4chenrouten (mapping2d) sind bearbeitbar")
    text = raw.decode("utf-8")
    prefix = _wpml_prefix(text, ns)

    # polygon: the <coordinates> block inside <Polygon>
    poly_at = text.find("<Polygon>")
    start = text.find("<coordinates>", poly_at)
    end = text.find("</coordinates>", start)
    if poly_at < 0 or start < 0 or end < 0:
        raise WpmlError("Vorlage enth\u00e4lt kein Polygon")
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
    if params.get("image_format"):
        # photo lenses of a thermal camera ("visable", "ir", "visable,ir"); only where the template has the tag
        if _find(root, ns, "imageFormat") is None:
            raise WpmlError("Vorlage hat kein Bildformat (imageFormat)")
        text = _replace_value(text, prefix, "imageFormat", params["image_format"])
    now = str(int(time.time() * 1000))
    text = _replace_value(text, prefix, "createTime", now)
    text = _replace_value(text, prefix, "updateTime", now)

    # sanity check: the edited template parses and carries exactly the requested polygon
    check = parse_template(text.encode("utf-8"))
    if check is None or len(check) != len(polygon) or any(
            abs(a[0] - b[0]) > 1e-9 or abs(a[1] - b[1]) > 1e-9 for a, b in zip(check, polygon)):
        raise WpmlError("interner Fehler: bearbeitetes Polygon nicht reproduzierbar")

    path = params.get("path")
    if path:
        # executable path generated from the planned lanes (Pilot 2 flies waylines.wpml as it is)
        for name in PATH_OFF_SWITCHES:
            text = _replace_value(text, prefix, name, "0")
        old = files.get(WAYLINES, b"").decode("utf-8")
        wl_prefix = _wpml_prefix(old, ns) if old else prefix
        lens_index = params.get("image_format") or (_find(root, ns, "imageFormat").text
                                                     if _find(root, ns, "imageFormat") is not None else "visable")
        waylines, info = build_waylines(old, wl_prefix, path["lanes"], new_height, params["speed"],
                                        path["photo_spacing"], lens_index.strip())
        files[WAYLINES] = waylines.encode("utf-8")
        params["path_info"] = info

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
