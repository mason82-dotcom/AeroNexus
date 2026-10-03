"""One inspection = many thermal images -> list of anomalies with position, Delta-T, class and image crop."""
import logging
import math
import uuid
from dataclasses import asdict
from typing import Callable

import numpy as np
from osgeo import gdal

from . import detect, geo, rjpeg
from .thermal import ThermalParams

log = logging.getLogger("pv.analysis")
gdal.UseExceptions()
Converter = Callable[[bytes, ThermalParams, float], np.ndarray]
CROP_MARGIN = 48                # px of the 1280 x 1024 colour JPEG around the spot
CROP_MIN = 200


def target_distance(shot: rjpeg.Shot) -> float:
    """Camera -> target distance for the atmospheric correction of the SDK."""
    if shot.has_lrf:
        return shot.lrf_distance
    h, _ = geo.height_above_plane(shot)
    sin_p = math.sin(math.radians(-shot.gimbal_pitch)) if shot.gimbal_pitch < -5 else 0.0
    return h / sin_p if sin_p > 0.1 else max(h, 5.0)


def _frame(img: np.ndarray, x0: int, y0: int, x1: int, y1: int) -> None:
    """Black outer, white inner line around the spot (img: bands x rows x cols)."""
    for off, val in ((0, 0), (1, 255), (2, 255), (3, 0)):
        a0, b0, a1, b1 = max(x0 - off, 0), max(y0 - off, 0), min(x1 + off, img.shape[2] - 1), min(y1 + off, img.shape[1] - 1)
        if a0 > a1 or b0 > b1:
            continue
        img[:, b0, a0:a1 + 1] = val
        img[:, b1, a0:a1 + 1] = val
        img[:, b0:b1 + 1, a0] = val
        img[:, b0:b1 + 1, a1] = val


def _png(img: np.ndarray, name: str) -> bytes:
    bands = img.shape[0]
    mem = gdal.GetDriverByName("MEM").Create("", img.shape[2], img.shape[1], bands, gdal.GDT_Byte)
    for i in range(bands):
        mem.GetRasterBand(i + 1).WriteArray(img[i])
    gdal.GetDriverByName("PNG").CreateCopy(name, mem)
    f = gdal.VSIFOpenL(name, "rb")
    gdal.VSIFSeekL(f, 0, 2)
    size = gdal.VSIFTellL(f)
    gdal.VSIFSeekL(f, 0, 0)
    data = gdal.VSIFReadL(1, size, f)
    gdal.VSIFCloseL(f)
    return data


def _cleanup(name: str) -> None:
    for ext in (".jpg", ".png", ".png.aux.xml"):
        if gdal.VSIStatL(name + ext):
            gdal.Unlink(name + ext)


def crop_png(jpeg: bytes, bbox: tuple[int, int, int, int]) -> bytes | None:
    """Cut the spot out of the colour JPEG (palette as in Pilot 2) and frame it."""
    name = f"/vsimem/pv-{uuid.uuid4()}"
    try:
        gdal.FileFromMemBuffer(name + ".jpg", jpeg)
        ds = gdal.Open(name + ".jpg")
        w, h = ds.RasterXSize, ds.RasterYSize
        sx, sy = w / rjpeg.RAW_W, h / rjpeg.RAW_H
        x0, y0, x1, y1 = bbox[0] * sx, bbox[1] * sy, bbox[2] * sx, bbox[3] * sy
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        half_w = max((x1 - x0) / 2 + CROP_MARGIN, CROP_MIN / 2)
        half_h = max((y1 - y0) / 2 + CROP_MARGIN, CROP_MIN / 2)
        ax, ay = int(max(0, cx - half_w)), int(max(0, cy - half_h))
        bx, by = int(min(w, cx + half_w)), int(min(h, cy + half_h))
        bands = min(ds.RasterCount, 3)
        img = np.stack([ds.GetRasterBand(i + 1).ReadAsArray(ax, ay, bx - ax, by - ay) for i in range(bands)])
        _frame(img, int(x0) - ax - 4, int(y0) - ay - 4, int(math.ceil(x1)) - ax + 3, int(math.ceil(y1)) - ay + 3)
        return _png(img, name + ".png")
    except RuntimeError as exc:
        log.warning("crop failed: %s", exc)
        return None
    finally:
        _cleanup(name)


VISIBLE_MAX_PX = 480            # width of the visible crop


def visible_crop(visible: bytes, thermal_f35: int | None, bbox: tuple[int, int, int, int]) -> bytes | None:
    """Same spot in the simultaneously taken visible photo (wide or zoom).

    The thermal and the visible camera look the same way (parallax of a few cm is negligible at inspection
    distance), so a raw thermal pixel maps to the visible photo by the ratio of the focal lengths in pixels:
    f_px = f35 * image diagonal / 43.27 mm. Returns None if the spot lies outside the visible photo (zoom).
    """
    f35_v = rjpeg.exif_focal(visible)[1]
    if not thermal_f35 or not f35_v:
        return None
    name = f"/vsimem/pv-{uuid.uuid4()}"
    try:
        gdal.FileFromMemBuffer(name + ".jpg", visible)
        ds = gdal.Open(name + ".jpg")
        wv, hv = ds.RasterXSize, ds.RasterYSize
        k = (f35_v * math.hypot(wv, hv)) / (thermal_f35 * math.hypot(rjpeg.RAW_W, rjpeg.RAW_H))

        def to_v(x, y):
            return wv / 2 + (x - rjpeg.RAW_W / 2) * k, hv / 2 + (y - rjpeg.RAW_H / 2) * k

        x0, y0 = to_v(bbox[0], bbox[1])
        x1, y1 = to_v(bbox[2], bbox[3])
        cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
        if not (0 <= cx < wv and 0 <= cy < hv):
            return None                                        # outside the (zoom) image
        half = max((x1 - x0) / 2, (y1 - y0) / 2) + (CROP_MARGIN / 2) * k      # same context as the thermal crop
        half = max(half, (CROP_MIN / 4) * k)
        ax, ay = int(max(0, cx - half)), int(max(0, cy - half))
        bx, by = int(min(wv, cx + half)), int(min(hv, cy + half))
        if bx - ax < 8 or by - ay < 8:
            return None
        scale = min(1.0, VISIBLE_MAX_PX / (bx - ax))
        ow, oh = max(1, int((bx - ax) * scale)), max(1, int((by - ay) * scale))
        bands = min(ds.RasterCount, 3)
        img = np.stack([ds.GetRasterBand(i + 1).ReadAsArray(ax, ay, bx - ax, by - ay, buf_xsize=ow, buf_ysize=oh)
                        for i in range(bands)])
        _frame(img, int((x0 - ax) * scale) - 3, int((y0 - ay) * scale) - 3,
               int(math.ceil((x1 - ax) * scale)) + 2, int(math.ceil((y1 - ay) * scale)) + 2)
        return _png(img, name + ".png")
    except RuntimeError as exc:
        log.warning("visible crop failed: %s", exc)
        return None
    finally:
        _cleanup(name)


def analyze_image(jpeg: bytes, file: dict, convert: Converter, tp: ThermalParams,
                  dp: detect.Params, visible: bytes | None = None) -> tuple[dict, list[dict]]:
    """Returns (image summary, anomalies). Anomalies carry their crops as bytes under "crop" / "crop_rgb"."""
    info = {"file_id": file["file_id"], "file_name": file["file_name"], "anomalies": 0}
    if not rjpeg.is_rjpeg(jpeg):
        info["skipped"] = "kein radiometrisches W\u00e4rmebild (R-JPEG)"
        return info, []
    shot = rjpeg.read_shot(jpeg)
    h, source = geo.height_above_plane(shot)
    info.update(model=shot.model, gps=shot.gps_valid, lat=shot.lat if shot.gps_valid else None,
                lon=shot.lon if shot.gps_valid else None, height_m=round(h, 1), height_source=source,
                gimbal_pitch=shot.gimbal_pitch, utc=shot.utc)
    temp = convert(jpeg, tp, target_distance(shot))
    thermal_f35 = rjpeg.exif_focal(jpeg)[1]
    info["visible"] = file.get("visible", {}).get("file_name") if file.get("visible") else None
    info.update(t_min=round(float(np.min(temp)), 1), t_median=round(float(np.median(temp)), 1),
                t_max=round(float(np.max(temp)), 1))
    out = []
    for s in detect.find_spots(temp, dp):
        gp = geo.pixel_to_ground(shot, s.x + 0.5, s.y + 0.5)
        area = long_m = short_m = None
        if gp:
            area = round(s.pixels * gp.gsd_m ** 2, 3)
            long_m, short_m = round(s.extent_px[0] * gp.gsd_m, 2), round(s.extent_px[1] * gp.gsd_m, 2)
        out.append({
            "id": uuid.uuid4().hex[:12], "file_id": file["file_id"], "file_name": file["file_name"],
            "x": round(s.x, 1), "y": round(s.y, 1), "bbox": list(s.bbox), "pixels": s.pixels,
            "t_max": s.t_max, "t_mean": s.t_mean, "t_background": s.t_background, "delta": s.delta,
            "klass": s.klass, "area_m2": area, "long_m": long_m, "short_m": short_m,
            "type": detect.guess_type(area, long_m, short_m),
            "lat": round(gp.lat, 8) if gp else None, "lon": round(gp.lon, 8) if gp else None,
            "height_source": gp.height_source if gp else None, "status": "open", "note": "",
            "seen_in": [file["file_name"]], "crop": crop_png(jpeg, s.bbox),
            "crop_rgb": visible_crop(visible, thermal_f35, s.bbox) if visible else None,
        })
    info["anomalies"] = len(out)
    return info, out


def merge(anomalies: list[dict], radius_m: float) -> list[dict]:
    """The same fault seen in overlapping images -> one anomaly (the strongest view, all image names)."""
    merged: list[dict] = []
    for a in sorted(anomalies, key=lambda a: -a["delta"]):
        if a["lat"] is not None:
            twin = next((m for m in merged if m["lat"] is not None and
                         geo.distance_m(m["lat"], m["lon"], a["lat"], a["lon"]) <= radius_m), None)
            if twin:
                twin["seen_in"] = sorted(set(twin["seen_in"]) | set(a["seen_in"]))
                continue
        merged.append(a)
    for i, a in enumerate(merged, 1):
        a["number"] = i
    return merged


def params_dict(tp: ThermalParams, dp: detect.Params, merge_m: float) -> dict:
    return {"thermal": asdict(tp), "detect": asdict(dp), "merge_m": merge_m}
