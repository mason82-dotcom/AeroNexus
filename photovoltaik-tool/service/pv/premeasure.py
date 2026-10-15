"""Thermal pre-measurement: temperature matrices of new R-JPEGs are computed once in the background.

The DJI Thermal SDK needs about 0.9 s per image on the Pi 5 (Box64), so an inspection of a few hundred images
waited minutes before anything appeared. The matrix depends on the image and on the measurement parameters
(emissivity, reflected temperature, humidity, distance). The cache key covers all of them, so an inspection
that uses the defaults finds the matrices measured here, and one with other parameters simply measures again.
Cache: <own bucket>/thermal-cache/<sha256 of the JPEG>_<params>.f32 (raw little-endian float32, 640x512).
"""
import hashlib
import logging
import threading

import numpy as np

from . import analysis, rjpeg, storage
from .thermal import ThermalError, ThermalParams

log = logging.getLogger("pv.premeasure")

THERMAL_SUFFIX = "_T.JPG"   # M3T / M4T R-JPEG (the visible sibling is _V / _W / _Z)
INTERVAL_SECONDS = 120


def cache_name(jpeg: bytes, tp: ThermalParams, distance: float) -> str:
    dist = tp.distance if tp.distance is not None else distance
    digest = hashlib.sha256(jpeg).hexdigest()
    return (f"{digest}_e{tp.emissivity:.3f}_r{tp.reflected_temp:.1f}_h{tp.humidity:.1f}"
            f"_d{round(dist, 1):.1f}.f32")


def cached_converter(base, cache_prefix: str):
    """Wrap converter(jpeg, tp, distance) -> ndarray with a matrix cache under cache_prefix."""
    def convert(jpeg: bytes, tp: ThermalParams, distance: float) -> np.ndarray:
        path = f"{cache_prefix}/{cache_name(jpeg, tp, distance)}"
        try:
            data = np.frombuffer(storage.read(path), dtype="<f4")
            if data.size == rjpeg.RAW_W * rjpeg.RAW_H:
                return data.reshape(rjpeg.RAW_H, rjpeg.RAW_W)
        except storage.NotFound:
            pass
        temp = base(jpeg, tp, distance)
        try:
            storage.write(path, np.ascontiguousarray(temp, dtype="<f4").tobytes())
        except Exception as exc:  # a failing cache must never fail the measurement
            log.warning("thermal cache write failed: %s", exc)
        return temp
    return convert


def _done_marker(cache_prefix: str, object_key: str) -> str:
    return f"{cache_prefix}/done/{hashlib.sha256(object_key.encode()).hexdigest()}"


def thermal_keys(media_root: str) -> list[str]:
    """Object keys of all thermal R-JPEG candidates in the media bucket (recursive listing)."""
    names = storage.list_recursive(media_root)
    return sorted(n for n in names if n.upper().endswith(THERMAL_SUFFIX))


def premeasure_pending(media_root: str, cache_prefix: str, convert, limit: int = 50) -> int:
    """Measure up to `limit` thermal images that have no done-marker yet. Returns the number measured."""
    measured = 0
    for key in thermal_keys(media_root):
        if measured >= limit:
            break
        marker = _done_marker(cache_prefix, key)
        try:
            storage.read(marker)
            continue                                    # handled in an earlier round
        except storage.NotFound:
            pass
        try:
            jpeg = storage.read(f"{media_root}/{key}")
        except storage.NotFound:
            continue                                    # listed but gone (deleted meanwhile)
        if rjpeg.is_rjpeg(jpeg):
            # same distance and defaults as an inspection with default parameters
            convert(jpeg, ThermalParams(), analysis.target_distance(rjpeg.read_shot(jpeg)))
            measured += 1
        storage.write(marker, key.encode())              # also for non-R-JPEGs: never look again
    return measured


def start(media_root: str, cache_prefix: str, convert, available, stop: threading.Event) -> threading.Thread:
    """Daemon thread: pre-measure every INTERVAL_SECONDS while the SDK is available."""
    def run():
        while not stop.is_set():
            try:
                if available():
                    while premeasure_pending(media_root, cache_prefix, convert) and not stop.is_set():
                        pass
            except ThermalError as exc:
                log.warning("thermal pre-measurement paused: %s", exc)
            except Exception as exc:
                log.warning("thermal pre-measurement round failed: %s", exc)
            stop.wait(INTERVAL_SECONDS)
    thread = threading.Thread(target=run, name="thermal-premeasure", daemon=True)
    thread.start()
    return thread
