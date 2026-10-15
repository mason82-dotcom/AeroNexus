"""Mavic 3M multispectral preparation: calibrated NDVI preview and statistics per shot.

Every shot of a Mavic 3M stores one 16-bit TIFF per band (*_MS_G/_R/_RE/_NIR.TIF) with the radiometric
metadata in DJI XMP. A background worker computes, once per shot, a reflectance-proportional NDVI on a
reduced grid (DJI M3M image processing guide):

    X_camera = (DN - BlackLevel) / 65535 * vignetting(r) / (SensorGain * ExposureTime[s])
    X_ref    = X_camera * SensorGainAdjustment (pCam) / Irradiance (sunlight sensor of that band)
    NDVI     = (NIR_ref - Red_ref) / (NIR_ref + Red_ref)

with vignetting(r) = 1 + sum k_i * r^(i+1) around CalibratedOpticalCenter, and both bands warped into the
common frame with their CalibratedHMatrix. The preview is a quick look for checking a field before an
orthomosaic job; distortion (DewarpData) is not removed.

Bands are found by content, not by name: Pilot 2 stores some uploads as org_<hash>_<ms>.tiff (seen 2026-10-15),
the DJI name only survives in cloud_sample.media_file. Every TIFF of the media bucket is looked at once; its
XMP gives the band (ImageSource MS_<band>_CAMERA, else BandName) and the shot (CaptureUUID, shared by all
files of one shutter release). Output in the own bucket (prefix ms-preview/):
  objects/<sha256 of object key>.json   object seen: {key, capture, band} (capture null = not a M3M band)
  captures/<capture>/<band>             object key of that band
  <capture>.png / <capture>.json        colour preview / statistics, bands present, calibration
"""
import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass

import numpy as np
from osgeo import gdal

gdal.UseExceptions()
log = logging.getLogger("farming.multispectral")

SCALE = 4                    # preview grid = full resolution / SCALE
INTERVAL_SECONDS = 120
BANDS = ("G", "R", "RE", "NIR")
SCAN_LIMIT = 200             # TIFF heads read per round
_TIFF_EXT = (".tif", ".tiff")
_SOURCE_RE = re.compile(r"^MS_(G|R|RE|NIR)_CAMERA$")
_BAND_NAMES = {"green": "G", "red": "R", "rededge": "RE", "nir": "NIR"}
_CAPTURE_RE = re.compile(r"^[0-9a-f]{32}$")
_ATTR_RE = re.compile(r'drone-dji:(\w+)="([^"]{0,400})"')
_ELEM_RE = re.compile(r"<drone-dji:(\w+)>([^<]{0,400})<")


@dataclass
class BandMeta:
    black_level: float
    gain: float
    exposure_s: float
    pcam: float
    irradiance: float
    center: tuple[float, float]
    vignetting: tuple[float, ...]
    hmatrix: np.ndarray       # 3x3, band pixel -> common frame (full resolution)


def parse_xmp(data: bytes) -> dict:
    """drone-dji attributes and elements (regex only, no XML parser on untrusted files)."""
    m = re.search(rb"<x:xmpmeta.*?</x:xmpmeta>", data, re.S)
    if not m:
        return {}
    text = m.group(0).decode("utf-8", "replace")
    return {**dict(_ELEM_RE.findall(text)), **dict(_ATTR_RE.findall(text))}


def band_meta(xmp: dict) -> BandMeta:
    try:
        h = [float(v) for v in xmp["CalibratedHMatrix"].split(",")]
        hmatrix = np.array(h, dtype=np.float64).reshape(3, 3) if len(h) == 9 else np.eye(3)
    except (KeyError, ValueError):
        hmatrix = np.eye(3)
    vignetting = tuple(float(v) for v in xmp.get("VignettingData", "").split(",") if v.strip())
    return BandMeta(
        black_level=float(xmp["BlackLevel"]),
        gain=float(xmp["SensorGain"]),
        exposure_s=float(xmp["ExposureTime"]) / 1e6,
        pcam=float(xmp.get("SensorGainAdjustment", 1.0)),
        irradiance=float(xmp["Irradiance"]),
        center=(float(xmp.get("CalibratedOpticalCenterX", 0)), float(xmp.get("CalibratedOpticalCenterY", 0))),
        vignetting=vignetting,
        hmatrix=hmatrix,
    )


def vignetting_factor(shape: tuple[int, int], meta: BandMeta, scale: int) -> np.ndarray:
    """1 + sum k_i r^(i+1), r in full-resolution pixels from the optical centre."""
    rows, cols = np.mgrid[0:shape[0], 0:shape[1]].astype(np.float64)
    x = (cols + 0.5) * scale - meta.center[0]
    y = (rows + 0.5) * scale - meta.center[1]
    r = np.hypot(x, y)
    factor = np.ones(shape, dtype=np.float64)
    for i, k in enumerate(meta.vignetting):
        factor += k * r ** (i + 1)
    return factor


def reflectance(dn: np.ndarray, meta: BandMeta, scale: int) -> np.ndarray:
    """Reflectance-proportional value of one band (see module docstring)."""
    camera = (dn.astype(np.float64) - meta.black_level) / 65535.0
    camera *= vignetting_factor(dn.shape, meta, scale)
    camera /= meta.gain * meta.exposure_s
    return camera * meta.pcam / meta.irradiance


def warp(img: np.ndarray, hmatrix: np.ndarray, scale: int) -> np.ndarray:
    """Band image -> common frame (inverse mapping, nearest neighbour); outside = NaN."""
    rows, cols = np.mgrid[0:img.shape[0], 0:img.shape[1]].astype(np.float64)
    pts = np.stack([(cols + 0.5) * scale, (rows + 0.5) * scale, np.ones_like(cols)])
    inv = np.linalg.inv(hmatrix)
    src = np.tensordot(inv, pts, axes=1)
    sx = src[0] / src[2] / scale - 0.5
    sy = src[1] / src[2] / scale - 0.5
    xi, yi = np.rint(sx).astype(int), np.rint(sy).astype(int)
    inside = (xi >= 0) & (yi >= 0) & (xi < img.shape[1]) & (yi < img.shape[0])
    out = np.full(img.shape, np.nan)
    out[inside] = img[yi[inside], xi[inside]]
    return out


def ndvi(nir: np.ndarray, red: np.ndarray) -> np.ndarray:
    with np.errstate(divide="ignore", invalid="ignore"):
        value = (nir - red) / (nir + red)
    value[~np.isfinite(value) | (nir + red <= 0)] = np.nan
    return np.clip(value, -1.0, 1.0)


def statistics(index: np.ndarray) -> dict:
    valid = index[np.isfinite(index)]
    if valid.size == 0:
        return {"valid_fraction": 0.0}
    return {"valid_fraction": round(valid.size / index.size, 3), "mean": round(float(valid.mean()), 4),
            "median": round(float(np.median(valid)), 4), "p05": round(float(np.percentile(valid, 5)), 4),
            "p95": round(float(np.percentile(valid, 95)), 4)}


def colorize(index: np.ndarray) -> np.ndarray:
    """RdYlGn-like ramp for NDVI -0.2 .. 0.9; NaN = black. Returns 3 x H x W uint8."""
    t = np.clip((index + 0.2) / 1.1, 0.0, 1.0)
    r = np.where(t < 0.5, 215, 215 - (t - 0.5) * 2 * 189)
    g = np.where(t < 0.5, 48 + t * 2 * 207, 255 - (t - 0.5) * 2 * 105)
    b = np.where(t < 0.5, 39 + t * 2 * 52, 91 - (t - 0.5) * 2 * 31)
    rgb = np.stack([r, g, b])
    rgb[:, ~np.isfinite(index)] = 0
    return np.nan_to_num(rgb).astype(np.uint8)


# ------------------------------------------------------------------ MinIO (GDAL /vsis3/)

def _read(path: str) -> bytes:
    f = gdal.VSIFOpenL(path, "rb")
    if f is None:
        raise FileNotFoundError(path)
    try:
        gdal.VSIFSeekL(f, 0, 2)
        size = gdal.VSIFTellL(f)
        gdal.VSIFSeekL(f, 0, 0)
        return gdal.VSIFReadL(1, size, f)
    finally:
        gdal.VSIFCloseL(f)


def _write(path: str, data: bytes) -> None:
    f = gdal.VSIFOpenL(path, "wb")
    try:
        gdal.VSIFWriteL(data, 1, len(data), f)
    finally:
        gdal.VSIFCloseL(f)


def _read_head(path: str, size: int = 512 * 1024) -> bytes:
    f = gdal.VSIFOpenL(path, "rb")
    if f is None:
        raise FileNotFoundError(path)
    try:
        return gdal.VSIFReadL(1, size, f)
    finally:
        gdal.VSIFCloseL(f)


def _band(path: str, scale: int) -> tuple[np.ndarray, BandMeta]:
    head = _read_head(path)                  # XMP sits before the pixel data
    meta = band_meta(parse_xmp(head))
    ds = gdal.Open(path)
    arr = ds.GetRasterBand(1).ReadAsArray(buf_xsize=ds.RasterXSize // scale, buf_ysize=ds.RasterYSize // scale,
                                         resample_alg=gdal.GRIORA_Average)
    return arr, meta


def band_of(xmp: dict) -> str | None:
    """Mavic 3M band (G, R, RE, NIR) of one file, None for anything else."""
    m = _SOURCE_RE.match(xmp.get("ImageSource", "").strip().upper())
    if m:
        return m.group(1)
    return _BAND_NAMES.get(re.sub(r"[\s_-]", "", xmp.get("BandName", "")).lower())


def capture_of(xmp: dict) -> str | None:
    capture = xmp.get("CaptureUUID", "").strip().lower().replace("-", "")
    return capture if _CAPTURE_RE.match(capture) else None


def _object_marker(out_prefix: str, key: str) -> str:
    return f"{out_prefix}/objects/{hashlib.sha256(key.encode()).hexdigest()}.json"


def object_record(out_prefix: str, key: str) -> dict:
    """{key, capture, band} of a media object seen by the scan (FileNotFoundError if not yet)."""
    return json.loads(_read(_object_marker(out_prefix, key)))


def previews_for(out_prefix: str, keys: list[str]) -> dict[str, dict | None]:
    """{object key: preview JSON of its capture, or None (no M3M band, not scanned yet, preview pending)}."""
    out: dict[str, dict | None] = {}
    loaded: dict[str, dict | None] = {}
    for key in dict.fromkeys(keys):
        out[key] = None
        if not key.lower().endswith(_TIFF_EXT):
            continue
        try:
            capture = object_record(out_prefix, key).get("capture")
        except (FileNotFoundError, ValueError):
            continue
        if capture and capture not in loaded:
            try:
                loaded[capture] = json.loads(_read(f"{out_prefix}/{capture}.json"))
            except (FileNotFoundError, ValueError):
                loaded[capture] = None
        out[key] = loaded.get(capture) if capture else None
    return out


def scan_new(media_root: str, out_prefix: str, limit: int = SCAN_LIMIT, seen: set | None = None) -> int:
    """Read the XMP of up to `limit` TIFFs not seen before and file the M3M bands by capture. Returns the count."""
    seen = set() if seen is None else seen
    names = [n for n in (gdal.ReadDirRecursive(media_root) or []) if n.lower().endswith(_TIFF_EXT)]
    scanned = 0
    for key in sorted(names):
        if scanned >= limit:
            break
        if key in seen:
            continue
        try:
            object_record(out_prefix, key)
            seen.add(key)
            continue
        except FileNotFoundError:
            pass
        try:
            xmp = parse_xmp(_read_head(f"{media_root}/{key}"))
        except FileNotFoundError:
            continue                                   # listed but gone (deleted meanwhile)
        band, capture = band_of(xmp), capture_of(xmp)
        if band and capture:
            _write(f"{out_prefix}/captures/{capture}/{band}", key.encode())
        else:
            capture = band = None
        _write(_object_marker(out_prefix, key), json.dumps({"key": key, "capture": capture, "band": band}).encode())
        seen.add(key)
        scanned += 1
    return scanned


def captures(out_prefix: str) -> dict[str, dict[str, str]]:
    """{capture: {band: object key}} of all bands filed by scan_new."""
    out: dict[str, dict[str, str]] = {}
    for name in gdal.ReadDirRecursive(f"{out_prefix}/captures") or []:
        parts = name.rstrip("/").split("/")
        if len(parts) == 2 and parts[1] in BANDS:
            out.setdefault(parts[0], {})[parts[1]] = _read(f"{out_prefix}/captures/{name}").decode()
    return out


def process_capture(media_root: str, out_prefix: str, capture: str, bands: dict[str, str],
                    scale: int = SCALE) -> dict:
    nir, nir_meta = _band(f"{media_root}/{bands['NIR']}", scale)
    red, red_meta = _band(f"{media_root}/{bands['R']}", scale)
    nir_ref = warp(reflectance(nir, nir_meta, scale), nir_meta.hmatrix, scale)
    red_ref = warp(reflectance(red, red_meta, scale), red_meta.hmatrix, scale)
    index = ndvi(nir_ref, red_ref)
    rgb = colorize(index)
    mem = gdal.GetDriverByName("MEM").Create("", rgb.shape[2], rgb.shape[1], 3, gdal.GDT_Byte)
    for i in range(3):
        mem.GetRasterBand(i + 1).WriteArray(rgb[i])
    png = f"/vsimem/{capture}.png"
    gdal.GetDriverByName("PNG").CreateCopy(png, mem)
    _write(f"{out_prefix}/{capture}.png", _read(png))
    gdal.Unlink(png)
    result = {"capture": capture, "index": "NDVI", "bands_present": sorted(bands, key=BANDS.index),
              "objects": {b: bands[b] for b in sorted(bands, key=BANDS.index)},
              "grid_scale": scale, "stats": statistics(index),
              "irradiance": {"NIR": nir_meta.irradiance, "R": red_meta.irradiance},
              "calibration": "black level, vignetting, gain/exposure, pCam, sunlight sensor; band alignment by "
                             "CalibratedHMatrix; no dewarp"}
    _write(f"{out_prefix}/{capture}.json", json.dumps(result).encode())
    return result


def process_pending(media_root: str, out_prefix: str, limit: int = 20, seen: set | None = None) -> int:
    """Scan new TIFFs, then compute up to `limit` previews of captures with NIR and red. Returns the count."""
    scan_new(media_root, out_prefix, seen=seen)
    done = 0
    for capture, bands in sorted(captures(out_prefix).items()):
        if done >= limit:
            break
        if "NIR" not in bands or "R" not in bands:
            continue
        try:
            _read(f"{out_prefix}/{capture}.json")
            continue
        except FileNotFoundError:
            pass
        try:
            result = process_capture(media_root, out_prefix, capture, bands)
            log.info("multispectral: %s NDVI mean %s", capture, result["stats"].get("mean"))
        except Exception as exc:  # one broken shot must not block the others
            log.warning("multispectral: %s failed: %s", capture, exc)
            _write(f"{out_prefix}/{capture}.json", json.dumps({"capture": capture, "error": str(exc)[:200]}).encode())
        done += 1
    return done


def start(media_root: str, out_prefix: str, stop: threading.Event) -> threading.Thread:
    seen: set = set()                     # object keys already filed (saves a marker read per round)

    def run():
        while not stop.is_set():
            try:
                while process_pending(media_root, out_prefix, seen=seen) and not stop.is_set():
                    pass
            except Exception as exc:
                log.warning("multispectral round failed: %s", exc)
            stop.wait(INTERVAL_SECONDS)
    thread = threading.Thread(target=run, name="multispectral", daemon=True)
    thread.start()
    return thread
