"""DJI radiometric JPEG (R-JPEG) of the M3T / M4T thermal camera: metadata and raw sensor data.

An R-JPEG is a normal colour JPEG (1280 x 1024, palette chosen in Pilot 2, upscaled 2x) plus
  APP3 segments   raw 16 bit sensor values of the 640 x 512 microbolometer (input of the DJI Thermal SDK)
  XMP drone-dji:* position, altitude, gimbal angles, laser rangefinder (LRF) target
  EXIF            focal length
Temperatures in deg C come from the DJI Thermal SDK (thermal.py); this module only reads the file.
"""
import re
import struct
from dataclasses import dataclass

import numpy as np

RAW_W, RAW_H = 640, 512
PIXEL_PITCH_MM = 0.012          # 12 um VOx sensor of M3T and M4T
_XMP_RE = re.compile(r'drone-dji:(\w+)="([^"]*)"')
_EXIF_FOCAL = 0x920A


class RJpegError(ValueError):
    pass


@dataclass
class Shot:
    model: str                  # M3T, M4T
    image_source: str           # InfraredCamera
    lat: float
    lon: float
    gps_valid: bool
    abs_alt: float              # m, as reported (GpsFusionAlt)
    rel_alt: float              # m above take-off point
    gimbal_yaw: float           # deg, absolute heading of the camera
    gimbal_pitch: float         # deg, -90 = straight down
    gimbal_roll: float
    lrf_distance: float         # m, 0 = no valid measurement
    lrf_lat: float
    lrf_lon: float
    lrf_abs_alt: float
    focal_mm: float
    utc: str

    @property
    def focal_px(self) -> float:
        """Focal length in pixels of the 640 x 512 raw grid."""
        return self.focal_mm / PIXEL_PITCH_MM

    @property
    def has_lrf(self) -> bool:
        return self.lrf_distance > 0.5 and (self.lrf_lat != 0 or self.lrf_lon != 0)


def _segments(data: bytes):
    """(marker, payload) of the JPEG header segments up to the scan."""
    if data[:2] != b"\xff\xd8":
        raise RJpegError("keine JPEG-Datei")
    i = 2
    while i + 4 <= len(data) and data[i] == 0xFF:
        marker = data[i + 1]
        if marker == 0xDA:          # start of scan: header ends here
            return
        length = struct.unpack(">H", data[i + 2:i + 4])[0]
        yield marker, data[i + 4:i + 2 + length]
        i += 2 + length


def raw_thermal(data: bytes) -> np.ndarray:
    """Raw 16 bit sensor values (512 x 640) from the APP3 segments."""
    raw = b"".join(p for m, p in _segments(data) if m == 0xE3)
    if len(raw) < RAW_W * RAW_H * 2:
        raise RJpegError("kein radiometrisches JPEG (R-JPEG): Rohdaten fehlen")
    return np.frombuffer(raw[:RAW_W * RAW_H * 2], dtype="<u2").reshape(RAW_H, RAW_W)


def is_rjpeg(data: bytes) -> bool:
    try:
        return sum(len(p) for m, p in _segments(data) if m == 0xE3) >= RAW_W * RAW_H * 2
    except RJpegError:
        return False


def _exif_focal_mm(data: bytes) -> float | None:
    for marker, payload in _segments(data):
        if marker != 0xE1 or not payload.startswith(b"Exif\x00\x00"):
            continue
        tiff = payload[6:]
        endian = "<" if tiff[:2] == b"II" else ">"

        def ifd_entries(offset):
            if offset <= 0 or offset + 2 > len(tiff):
                return []
            n = struct.unpack(endian + "H", tiff[offset:offset + 2])[0]
            out = []
            for k in range(n):
                e = offset + 2 + 12 * k
                if e + 12 > len(tiff):
                    break
                tag, typ, count, value = struct.unpack(endian + "HHII", tiff[e:e + 12])
                out.append((tag, typ, count, value))
            return out

        ifd0 = struct.unpack(endian + "I", tiff[4:8])[0]
        for tag, typ, count, value in ifd_entries(ifd0):
            if tag != 0x8769:          # Exif sub-IFD
                continue
            for t2, typ2, c2, v2 in ifd_entries(value):
                if t2 == _EXIF_FOCAL and typ2 == 5 and v2 + 8 <= len(tiff):   # RATIONAL
                    num, den = struct.unpack(endian + "II", tiff[v2:v2 + 8])
                    return num / den if den else None
    return None


def _xmp(data: bytes) -> dict[str, str]:
    start = data.find(b"<x:xmpmeta")
    end = data.find(b"</x:xmpmeta>", start)
    if start < 0 or end < 0:
        return {}
    return dict(_XMP_RE.findall(data[start:end].decode("latin-1")))


def _f(x: dict, key: str, default: float = 0.0) -> float:
    try:
        return float(x.get(key, default))
    except ValueError:
        return default


# focal lengths of the thermal cameras, used when the EXIF tag is missing
_FOCAL_BY_MODEL = {"M3T": 9.1, "M4T": 12.0}


def read_shot(data: bytes) -> Shot:
    x = _xmp(data)
    if not x:
        raise RJpegError("keine DJI-Metadaten (XMP) im Bild")
    model = x.get("DroneModel", "")
    focal = _exif_focal_mm(data) or _FOCAL_BY_MODEL.get(model)
    if not focal:
        raise RJpegError(f"Brennweite unbekannt (Modell {model or '?'})")
    lat, lon = _f(x, "GpsLatitude"), _f(x, "GpsLongitude")
    return Shot(
        model=model, image_source=x.get("ImageSource", ""),
        lat=lat, lon=lon, gps_valid=x.get("GpsStatus", "").lower() != "invalid" and (lat != 0 or lon != 0),
        abs_alt=_f(x, "AbsoluteAltitude"), rel_alt=_f(x, "RelativeAltitude"),
        gimbal_yaw=_f(x, "GimbalYawDegree"), gimbal_pitch=_f(x, "GimbalPitchDegree"),
        gimbal_roll=_f(x, "GimbalRollDegree"),
        lrf_distance=_f(x, "LRFTargetDistance"), lrf_lat=_f(x, "LRFTargetLat"), lrf_lon=_f(x, "LRFTargetLon"),
        lrf_abs_alt=_f(x, "LRFTargetAbsAlt"), focal_mm=float(focal), utc=x.get("UTCAtExposure", ""),
    )
