"""Pixel of a thermal image -> position on the ground (WGS84).

Pinhole camera on the stabilised gimbal (roll ignored, it stays ~0), ray intersected with a horizontal plane:
  - plane at the laser rangefinder target height if the LRF measured (roofs, slopes: right height at the
    image centre), otherwise
  - plane at take-off height (relative altitude), good for ground-mounted plants on flat terrain.
Frames: camera x right, y down, z forward; world N, E, D (north, east, down).
"""
import math
from dataclasses import dataclass

from .rjpeg import RAW_H, RAW_W, Shot

M_PER_DEG_LAT = 111_320.0
MIN_DOWN = 0.05                 # rays flatter than ~3 deg below the horizon are not projected


@dataclass
class GroundPoint:
    lat: float
    lon: float
    distance_m: float           # slant distance camera -> point
    gsd_m: float                # ground size of one raw pixel at this point (approx.)
    height_source: str          # "lrf" or "takeoff"


def _axes(yaw_deg: float, pitch_deg: float):
    """Camera axes (right, down, forward) as N/E/D vectors."""
    yaw, pitch = math.radians(yaw_deg), math.radians(pitch_deg)
    # after pitch (yaw 0): forward = (cos p, 0, -sin p), image-down = (sin p, 0, cos p), right = east
    fwd = (math.cos(pitch), 0.0, -math.sin(pitch))
    down = (math.sin(pitch), 0.0, math.cos(pitch))
    right = (0.0, 1.0, 0.0)

    def rot(v):     # yaw about the down axis
        n, e, d = v
        return (n * math.cos(yaw) - e * math.sin(yaw), n * math.sin(yaw) + e * math.cos(yaw), d)
    return rot(right), rot(down), rot(fwd)


def height_above_plane(shot: Shot) -> tuple[float, str]:
    if shot.has_lrf:
        h = shot.abs_alt - shot.lrf_abs_alt
        if h > 1.0:
            return h, "lrf"
    return shot.rel_alt, "takeoff"


def pixel_to_ground(shot: Shot, px: float, py: float, width: int = RAW_W, height: int = RAW_H) -> GroundPoint | None:
    """px/py in the raw grid (0..640, 0..512). None if the ray does not hit the plane."""
    h, source = height_above_plane(shot)
    if h <= 0.5 or not shot.gps_valid:
        return None
    f = shot.focal_px * width / RAW_W
    u, v = (px - width / 2) / f, (py - height / 2) / f
    right, down, fwd = _axes(shot.gimbal_yaw, shot.gimbal_pitch)
    ray = tuple(fwd[i] + u * right[i] + v * down[i] for i in range(3))
    norm = math.sqrt(sum(c * c for c in ray))
    ray = tuple(c / norm for c in ray)
    if ray[2] < MIN_DOWN:
        return None
    t = h / ray[2]
    north, east = t * ray[0], t * ray[1]
    lat = shot.lat + north / M_PER_DEG_LAT
    lon = shot.lon + east / (M_PER_DEG_LAT * math.cos(math.radians(shot.lat)))
    # pixel footprint: slant distance / focal length, stretched by the incidence angle on the plane
    gsd = t / f / math.sqrt(max(ray[2], MIN_DOWN))
    return GroundPoint(lat=lat, lon=lon, distance_m=t, gsd_m=gsd, height_source=source)


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    dn = (lat2 - lat1) * M_PER_DEG_LAT
    de = (lon2 - lon1) * M_PER_DEG_LAT * math.cos(math.radians((lat1 + lat2) / 2))
    return math.hypot(dn, de)
