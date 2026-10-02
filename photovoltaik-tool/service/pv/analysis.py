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
        # frame: black outer, white inner line around the spot
        fx0, fy0 = int(x0) - ax - 4, int(y0) - ay - 4
        fx1, fy1 = int(math.ceil(x1)) - ax + 3, int(math.ceil(y1)) - ay + 3
        for off, val in ((0, 0), (1, 255), (2, 255), (3, 0)):
            a0, b0, a1, b1 = fx0 - off, fy0 - off, fx1 + off, fy1 + off
            a0, b0 = max(a0, 0), max(b0, 0)
            a1, b1 = min(a1, img.shape[2] - 1), min(b1, img.shape[1] - 1)
            img[:, b0, a0:a1 + 1] = val
            img[:, b1, a0:a1 + 1] = val
            img[:, b0:b1 + 1, a0] = val
            img[:, b0:b1 + 1, a1] = val
        mem = gdal.GetDriverByName("MEM").Create("", img.shape[2], img.shape[1], bands, gdal.GDT_Byte)
        for i in range(bands):
            mem.GetRasterBand(i + 1).WriteArray(img[i])
        gdal.GetDriverByName("PNG").CreateCopy(name + ".png", mem)
        f = gdal.VSIFOpenL(name + ".png", "rb")
        gdal.VSIFSeekL(f, 0, 2)
        size = gdal.VSIFTellL(f)
        gdal.VSIFSeekL(f, 0, 0)
        data = gdal.VSIFReadL(1, size, f)
        gdal.VSIFCloseL(f)
        return data
    except RuntimeError as exc:
        log.warning("crop failed: %s", exc)
        return None
    finally:
        for ext in (".jpg", ".png", ".png.aux.xml"):
            if gdal.VSIStatL(name + ext):
                gdal.Unlink(name + ext)


def analyze_image(jpeg: bytes, file: dict, convert: Converter, tp: ThermalParams,
                  dp: detect.Params) -> tuple[dict, list[dict]]:
    """Returns (image summary, anomalies). Anomalies carry their crop as bytes under "crop"."""
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
