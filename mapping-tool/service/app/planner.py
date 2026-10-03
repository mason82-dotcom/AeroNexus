"""Flight planning for area routes (mapping2d): camera table, height from target ground resolution,
speed from the photo interval, preview of the flight lines and statistics.

The executable route is always computed by Pilot 2 from the template (polygon, height, overlap, direction);
the preview here is an estimate for planning (number of lines, length, time, photos).
GSD factors (ground sample distance per metre of height) follow DJI's specifications:
  M3E/M3M RGB H/37.5 cm, M3M multispectral H/18.9 cm, M3T thermal H/7.6 cm; M4T thermal 12 mm / 12 um.
"""
import math
from dataclasses import dataclass

M_PER_DEG = 111_320.0
LEGAL_MAX_HEIGHT = 120.0        # m above ground, EU open category
SHOT_INTERVAL_S = 2.0           # shortest timed-shot interval of Pilot 2 for these cameras


@dataclass(frozen=True)
class Lens:
    key: str
    label: str
    gsd_per_m: float            # m of ground per pixel and metre of height
    width_px: int               # across track (long image side)
    height_px: int              # along track


# payload type (DeviceEnum "1-<type>-<sub>", sub ignored) -> lenses of the camera
CAMERAS: dict[int, tuple[str, list[Lens]]] = {
    66: ("Mavic 3E", [Lens("wide", "Weitwinkel (RGB)", 0.01 / 37.5, 5280, 3956)]),
    67: ("Mavic 3T", [Lens("wide", "Weitwinkel (RGB)", 0.0016 / 4.27, 4000, 3000),
                      Lens("thermal", "W\u00e4rmebild", 0.012 / 9.1, 640, 512)]),
    68: ("Mavic 3M", [Lens("wide", "RGB", 0.01 / 37.5, 5280, 3956),
                      Lens("ms", "Multispektral", 0.01 / 18.9, 1600, 1300)]),
    88: ("Matrice 4E", [Lens("wide", "Weitwinkel (RGB)", 0.01 / 37.5, 5280, 3956)]),
    89: ("Matrice 4T", [Lens("wide", "Weitwinkel (RGB)", 0.0024 / 6.4, 4032, 3024),
                        Lens("thermal", "W\u00e4rmebild", 0.012 / 12.0, 640, 512)]),
}

# purpose -> lens, target GSD (cm), overlaps (%), speed limit (m/s), image format (thermal cameras only)
PRESETS: dict[str, dict] = {
    "pv_thermal": {"label": "PV-Thermografie", "lens": "thermal", "gsd_cm": 3.0, "overlap_h": 75, "overlap_w": 50,
                   "max_speed": 5.0, "image_format": "visable,ir",
                   "hint": "Mittags bei Sonne (> 600 W/m\u00b2), Flugrichtung parallel zu den Modulreihen."},
    "multispectral": {"label": "Multispektral (Farming Guide)", "lens": "ms", "gsd_cm": 5.0, "overlap_h": 80,
                      "overlap_w": 70, "max_speed": 10.0, "image_format": None,
                      "hint": "Gleichm\u00e4\u00dfiges Licht, Sonnensensor frei; m\u00f6glichst um die Mittagszeit."},
    "ortho": {"label": "Orthofoto (RGB)", "lens": "wide", "gsd_cm": 2.0, "overlap_h": 80, "overlap_w": 70,
              "max_speed": 12.0, "image_format": "visable",
              "hint": "Wenig Wind, gleichm\u00e4\u00dfige Beleuchtung."},
}
IMAGE_FORMATS = ("visable", "ir", "visable,ir")


class PlanError(ValueError):
    pass


def payload_type(payload_key: str | None) -> int | None:
    """'1-67-0' / '67-0' / 67 -> 67."""
    if payload_key is None:
        return None
    parts = str(payload_key).split("-")
    try:
        return int(parts[1]) if len(parts) == 3 else int(parts[0])
    except (ValueError, IndexError):
        return None


def camera(payload_key) -> tuple[str, list[Lens]]:
    t = payload_type(payload_key)
    if t not in CAMERAS:
        raise PlanError(f"Kamera {payload_key} wird f\u00fcr die Planung nicht unterst\u00fctzt")
    return CAMERAS[t]


def lens(payload_key, key: str) -> Lens:
    for ln in camera(payload_key)[1]:
        if ln.key == key:
            return ln
    raise PlanError(f"Objektiv {key} gibt es an dieser Kamera nicht")


def presets_for(payload_key) -> dict[str, dict]:
    keys = {ln.key for ln in camera(payload_key)[1]}
    return {k: v for k, v in PRESETS.items() if v["lens"] in keys}


def height_for_gsd(ln: Lens, gsd_cm: float) -> float:
    return gsd_cm / 100.0 / ln.gsd_per_m


def gsd_cm(ln: Lens, height: float) -> float:
    return height * ln.gsd_per_m * 100.0


def footprint_m(ln: Lens, height: float) -> tuple[float, float]:
    """(across track, along track) ground size of one photo."""
    g = height * ln.gsd_per_m
    return ln.width_px * g, ln.height_px * g


def speed_for(ln: Lens, height: float, overlap_h: float, max_speed: float) -> float:
    along = footprint_m(ln, height)[1]
    return round(max(1.0, min(max_speed, along * (1 - overlap_h / 100.0) / SHOT_INTERVAL_S)), 1)


# ------------------------------------------------------------------ geometry (local metres around the centroid)

def _local(poly: list[list[float]]):
    lon0 = sum(p[0] for p in poly) / len(poly)
    lat0 = sum(p[1] for p in poly) / len(poly)
    k = math.cos(math.radians(lat0))
    to_xy = lambda p: ((p[0] - lon0) * M_PER_DEG * k, (p[1] - lat0) * M_PER_DEG)          # noqa: E731
    to_ll = lambda x, y: [lon0 + x / (M_PER_DEG * k), lat0 + y / M_PER_DEG]               # noqa: E731
    return [to_xy(p) for p in poly], to_ll


def area_m2(poly: list[list[float]]) -> float:
    xy, _ = _local(poly)
    n = len(xy)
    return abs(sum(xy[i][0] * xy[(i + 1) % n][1] - xy[(i + 1) % n][0] * xy[i][1] for i in range(n))) / 2


def longest_edge_direction(poly: list[list[float]]) -> int:
    """Azimuth (0-179, clockwise from north) of the longest polygon edge: fewer, longer lines."""
    xy, _ = _local(poly)
    best, az = -1.0, 0.0
    for i in range(len(xy)):
        (x1, y1), (x2, y2) = xy[i], xy[(i + 1) % len(xy)]
        d = math.hypot(x2 - x1, y2 - y1)
        if d > best:
            best, az = d, math.degrees(math.atan2(x2 - x1, y2 - y1)) % 180
    return int(round(az)) % 180


def _intervals(hits: list[float], margin: float) -> list[tuple[float, float]]:
    """Inside parts of one sweep line (pairs of sorted edge crossings), widened by the margin and merged."""
    out: list[list[float]] = []
    for i in range(0, len(hits) - 1, 2):
        a, b = hits[i] - margin, hits[i + 1] + margin
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [(a, b) for a, b in out]


def flight_lines(poly: list[list[float]], direction: float, spacing: float, margin: float = 0.0):
    """Lawnmower lanes along `direction` (azimuth), `spacing` m apart, inside the polygon.
    Returns [[lon, lat], [lon, lat]] segments in flying order.

    Concave areas (U, L shapes) are split into cells where every sweep line has exactly one inside part
    (boustrophedon decomposition); each cell is flown as its own lawnmower, cells one after another, so no lane
    crosses a gap outside the area. Only the short transitions between cells do.
    """
    if spacing <= 0.5:
        raise PlanError("Linienabstand zu klein")
    xy, to_ll = _local(poly)
    a = math.radians(direction)
    # along = projection on the flight direction, across = perpendicular (to the right)
    along = lambda x, y: x * math.sin(a) + y * math.cos(a)          # noqa: E731
    across = lambda x, y: x * math.cos(a) - y * math.sin(a)         # noqa: E731
    back = lambda u, v: (u * math.sin(a) + v * math.cos(a), u * math.cos(a) - v * math.sin(a))  # (along, across) -> x, y  # noqa: E501
    pts = [(along(x, y), across(x, y)) for x, y in xy]
    vmin, vmax = min(p[1] for p in pts) - margin, max(p[1] for p in pts) + margin
    n_lines = max(1, int(math.ceil((vmax - vmin) / spacing - 1e-6)))     # tolerance: 100 m / 20 m = 5 lines
    first = vmin + ((vmax - vmin) - (n_lines - 1) * spacing) / 2

    # cells: consecutive sweep lines with the same number of inside parts that overlap pairwise
    cells: list[list[tuple[float, float, float]]] = []       # each cell: [(v, u0, u1), ...]
    active: list[int] = []
    for i in range(n_lines):
        v = first + i * spacing
        hits = []
        for j in range(len(pts)):
            (u1, v1), (u2, v2) = pts[j], pts[(j + 1) % len(pts)]
            if (v1 <= v < v2) or (v2 <= v < v1):
                hits.append(u1 + (v - v1) * (u2 - u1) / (v2 - v1))
        hits.sort()
        parts = _intervals(hits, margin)
        prev = [cells[c][-1] for c in active]
        same = len(parts) == len(prev) and all(p0 < q1 and q0 < p1 for (p0, p1), (_, q0, q1) in zip(parts, prev))
        if same:
            for c, (u0, u1) in zip(active, parts):
                cells[c].append((v, u0, u1))
        else:
            active = []
            for u0, u1 in parts:
                cells.append([(v, u0, u1)])
                active.append(len(cells) - 1)

    segments, pos = [], None
    for cell in cells:
        lanes = [(back(u0, v), back(u1, v)) for v, u0, u1 in cell]
        # enter the cell at the lane end nearest to where the previous cell ended
        if pos is not None:
            d = lambda p: math.hypot(p[0] - pos[0], p[1] - pos[1])      # noqa: E731
            options = {"fwd": d(lanes[0][0]), "fwd_rev": d(lanes[0][1]),
                       "bwd": d(lanes[-1][0]), "bwd_rev": d(lanes[-1][1])}
            best = min(options, key=options.get)
            if best.startswith("bwd"):
                lanes.reverse()
            flip_first = best.endswith("rev")
        else:
            flip_first = False
        for k, (p, q) in enumerate(lanes):
            if (k % 2 == 1) != flip_first:
                p, q = q, p
            segments.append([to_ll(*p), to_ll(*q)])
            pos = q
    return segments


def _seg_len(s) -> float:
    k = math.cos(math.radians(s[0][1]))
    return math.hypot((s[1][0] - s[0][0]) * M_PER_DEG * k, (s[1][1] - s[0][1]) * M_PER_DEG)


def preview(poly: list[list[float]], payload_key, lens_key: str, height: float, overlap_h: float,
            overlap_w: float, direction: float, speed: float, margin: float = 0.0) -> dict:
    if len(poly) < 3:
        raise PlanError("Fl\u00e4che braucht mindestens 3 Ecken")
    ln = lens(payload_key, lens_key)
    across_m, along_m = footprint_m(ln, height)
    spacing = across_m * (1 - overlap_w / 100.0)
    lines = flight_lines(poly, direction, spacing, margin)
    on_line = sum(_seg_len(s) for s in lines)
    turns = sum(_seg_len([lines[i][1], lines[i + 1][0]]) for i in range(len(lines) - 1))
    total = on_line + turns
    photo_step = along_m * (1 - overlap_h / 100.0)
    warnings = []
    if height > LEGAL_MAX_HEIGHT:
        warnings.append(f"H\u00f6he \u00fcber {LEGAL_MAX_HEIGHT:.0f} m (offene Kategorie)")
    max_speed = photo_step / SHOT_INTERVAL_S
    if speed > max_speed + 0.05:
        warnings.append(f"Bei {speed:.1f} m/s reicht die L\u00e4ngs\u00fcberlappung nicht (max. {max_speed:.1f} m/s)")
    return {
        "lens": ln.key, "gsd_cm": round(gsd_cm(ln, height), 2),
        "footprint_m": [round(across_m, 1), round(along_m, 1)], "line_spacing_m": round(spacing, 1),
        "lines": lines, "line_count": len(lines), "length_m": round(total), "area_ha": round(area_m2(poly) / 1e4, 3),
        "duration_min": round(total / max(speed, 0.1) / 60 + len(lines) * 5 / 60, 1),    # ~5 s per turn
        "photos": int(on_line / photo_step) + len(lines) if photo_step > 0 else 0,
        "warnings": warnings,
    }
