"""Photo parameters from EXIF and DJI XMP (drone-dji:*), normalised for the web media list.

XMP is read with a regular expression (attributes only), never with an XML parser, so the
untrusted file content cannot trigger entity expansion.
"""
import io
import re
from datetime import datetime

from PIL import Image

HEAD_BYTES = 256 * 1024
VERSION = 2                                       # bump when fields change: cached rows are re-extracted
_DJI_RE = re.compile(r'dji:(ImageSource|BandName|BandFreq)="([^"]{0,60})"')

# dji:ImageSource -> label; unknown values are shown as they are
_SOURCES = {
    "WideCamera": "Wide", "ZoomCamera": "Zoom", "TeleCamera": "Tele", "InfraredCamera": "IR (thermal)",
    "VisibleCamera": "Visible", "MS_CAMERA": "Multispectral", "RGBCamera": "RGB",
}
# DJI file name suffix -> label (fallback when the XMP has no ImageSource, e.g. Mavic 3M _D)
_SUFFIXES = {
    "W": "Wide", "Z": "Zoom", "T": "IR (thermal)", "V": "Visible", "D": "RGB", "S": "Split screen",
    "F": "Multispectral", "MS_G": "MS Green", "MS_R": "MS Red", "MS_RE": "MS Red edge", "MS_NIR": "MS NIR",
}
_SUFFIX_RE = re.compile(r"_([A-Z]+(?:_[A-Z]+)?)\.[A-Za-z0-9]+$")


def lens(source: str | None, band: str | None, freq: str | None, file_name: str) -> str | None:
    if source and source.startswith("MS_") and source != "MS_CAMERA" and band:
        return f"MS {band}" + (f" {freq}" if freq else "")
    if source:
        return _SOURCES.get(source, source)
    m = _SUFFIX_RE.search(file_name)
    return _SUFFIXES.get(m.group(1)) if m else None
_XMP_RE = re.compile(rb"<x:xmpmeta.*?</x:xmpmeta>", re.S)
_ATTR_RE = re.compile(r'drone-dji:(\w+)="([^"]{0,200})"')
_ELEM_RE = re.compile(r"<drone-dji:(\w+)>([^<]{0,200})<")


def _num(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _ratio(value):
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError, ZeroDivisionError):
        return None


def xmp(data: bytes) -> dict[str, str]:
    m = _XMP_RE.search(data)
    if not m:
        return {}
    text = m.group(0).decode("utf-8", errors="replace")
    return {**dict(_ELEM_RE.findall(text)), **dict(_ATTR_RE.findall(text))}


def exif(data: bytes) -> dict:
    img = Image.open(io.BytesIO(data))           # reads the header only
    ex = img.getexif()
    out = {"width": img.size[0], "height": img.size[1]}
    for tag, value in ex.items():
        out[tag] = value
    for tag, value in ex.get_ifd(0x8769).items():     # Exif sub-IFD
        out[tag] = value
    return out


def extract(data: bytes, size_bytes: int | None, file_name: str = "") -> dict:
    """data: file head (or whole file). Raises if neither EXIF nor XMP is readable."""
    try:
        e = exif(data)
    except Exception:
        e = {}
    x = xmp(data)
    if not e and not x:
        raise ValueError("no EXIF/XMP in the given bytes")
    captured = e.get(0x9003)                          # DateTimeOriginal "YYYY:MM:DD HH:MM:SS" (local time)
    try:
        captured = datetime.strptime(captured, "%Y:%m:%d %H:%M:%S").isoformat() if captured else None
    except ValueError:
        captured = None
    gps_status = x.get("GpsStatus") or None
    lat, lon = _num(x.get("GpsLatitude")), _num(x.get("GpsLongitude"))
    if gps_status == "Invalid" or (lat is not None and abs(lat) < 1e-4 and lon is not None and abs(lon) < 1e-4):
        lat = lon = None
    std = [_num(x.get(k)) for k in ("RtkStdLon", "RtkStdLat")]
    m = _XMP_RE.search(data)
    dji = dict(_DJI_RE.findall(m.group(0).decode("utf-8", errors="replace"))) if m else {}
    return {
        "v": VERSION,
        "lens": lens(dji.get("ImageSource"), dji.get("BandName"), dji.get("BandFreq"), file_name),
        "image_source": dji.get("ImageSource"),
        "band": dji.get("BandName"),
        "band_freq": dji.get("BandFreq"),
        "captured": captured,
        "model": e.get(0x0110) or x.get("DroneModel"),
        "width": e.get(0xA002) or e.get("width"),
        "height": e.get(0xA003) or e.get("height"),
        "exposure_time": _ratio(e.get(0x829A)),
        "f_number": _ratio(e.get(0x829D)),
        "iso": e.get(0x8827),
        "focal_mm": _ratio(e.get(0x920A)),
        "focal_35mm": e.get(0xA405),
        "shutter": x.get("ShutterType"),
        "gps_status": gps_status,
        "lat": lat,
        "lon": lon,
        "abs_alt": _num(x.get("AbsoluteAltitude")),
        "rel_alt": _num(x.get("RelativeAltitude")),
        "altitude_type": x.get("AltitudeType"),
        "rtk_std_m": round(max(s for s in std if s is not None), 3) if any(s is not None for s in std) else None,
        "gimbal_pitch": _num(x.get("GimbalPitchDegree")),
        "gimbal_yaw": _num(x.get("GimbalYawDegree")),
        "flight_yaw": _num(x.get("FlightYawDegree")),
        "lrf_distance": _num(x.get("LRFTargetDistance")) or None,
        "size_bytes": size_bytes,
    }
