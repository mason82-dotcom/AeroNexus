"""Farming Guide: relative vegetation indices from a multispectral ODM orthophoto.

Definitions come from farming-guide/indices.json (normalised differences of two bands). Work is done
block-wise by GDAL tools so large fields fit into 16 GB RAM; numpy only touches histograms.
"""
import json
import logging
import re
from pathlib import Path

from osgeo import gdal

from .postprocess import run, to_cog

log = logging.getLogger("agent.indices")
gdal.UseExceptions()
NODATA = -9999.0


def _norm(text: str) -> str:
    return re.sub(r"[\s_\-]+", "", (text or "").lower())


def load_definitions(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def band_map(ortho: Path, definitions: dict) -> tuple[dict[str, int], int | None]:
    """Band key (green/red/rededge/nir) -> 1-based band number, plus the alpha band number."""
    aliases = {key: {_norm(a) for a in names} for key, names in definitions["bands"].items()}
    ds = gdal.Open(str(ortho))
    found, alpha = {}, None
    for i in range(1, ds.RasterCount + 1):
        band = ds.GetRasterBand(i)
        if band.GetColorInterpretation() == gdal.GCI_AlphaBand:
            alpha = i
            continue
        desc = _norm(band.GetDescription())
        for key, names in aliases.items():
            if desc in names and key not in found:
                found[key] = i
    return found, alpha


def is_multispectral(ortho: Path, definitions: dict) -> bool:
    bands, _ = band_map(ortho, definitions)
    return "nir" in bands and "red" in bands


def _stats(path: Path) -> dict:
    ds = gdal.Open(str(path))
    band = ds.GetRasterBand(1)
    lo, hi, mean, std = band.ComputeStatistics(False)
    buckets = 512
    hist = band.GetHistogram(lo, hi, buckets, include_out_of_range=0, approx_ok=0)
    total = sum(hist)
    step = (hi - lo) / buckets if hi > lo else 0

    def pct(p: float) -> float:
        target, acc = total * p, 0
        for i, n in enumerate(hist):
            acc += n
            if acc >= target:
                return round(lo + (i + 0.5) * step, 4)
        return round(hi, 4)

    return {"mean": round(mean, 4), "std": round(std, 4), "min": round(lo, 4), "max": round(hi, 4),
            "p10": pct(0.10), "p50": pct(0.50), "p90": pct(0.90),
            "valid_ratio": round(total / (ds.RasterXSize * ds.RasterYSize), 4)}


def _color_file(index: dict, path: Path) -> None:
    lines = []
    for value, color in index["palette"]:
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        lines.append(f"{value} {r} {g} {b} 255")
    lines.append("nv 0 0 0 0")
    path.write_text("\n".join(lines) + "\n")


def false_color(ortho: Path, bands: dict[str, int], alpha: int | None, dst: Path) -> None:
    """NIR-Red-Green composite, 8 bit, each band stretched to its 2..98 % range (approximate stats)."""
    ds = gdal.Open(str(ortho))
    cmd = ["gdal_translate", "-q", "-ot", "Byte"]
    for i, key in enumerate(("nir", "red", "green"), 1):
        b = bands.get(key, bands["red"])
        band = ds.GetRasterBand(b)
        lo, hi, mean, std = band.ComputeStatistics(True)
        lo, hi = max(lo, mean - 2 * std), min(hi, mean + 2 * std)
        cmd += ["-b", str(b), f"-scale_{i}", str(lo), str(hi), "0", "255"]
    if alpha:
        cmd += ["-b", str(alpha), "-scale_4", "0", "1", "0", "255", "-colorinterp_4", "alpha"]
    run(cmd + [str(ortho), str(dst)], cwd=dst.parent)


def build(ortho: Path, work: Path, out: Path, definitions: dict, target_crs: str, minzoom: int, maxzoom: int,
          processes: int) -> tuple[list[dict], list[dict]]:
    """Returns (files, layers) for the manifest. Indices whose bands are missing are skipped."""
    bands, alpha = band_map(ortho, definitions)
    log.info("multispectral bands %s, alpha %s", bands, alpha)
    files, layers = [], []
    work.mkdir(parents=True, exist_ok=True)
    for index in definitions["indices"]:
        a, b = index["a"], index["b"]
        if a not in bands or b not in bands:
            log.warning("index %s skipped: band %s missing", index["id"], a if a not in bands else b)
            continue
        raw = work / f"{index['id']}_raw.tif"
        mask = f"(C > 0)" if alpha else "1"
        cmd = ["gdal_calc", "--quiet", "--overwrite", "--type=Float32", f"--NoDataValue={NODATA}",
               "-A", str(ortho), f"--A_band={bands[a]}", "-B", str(ortho), f"--B_band={bands[b]}"]
        if alpha:
            cmd += ["-C", str(ortho), f"--C_band={alpha}"]
        expr = (f"where({mask} * ((A.astype(float32) + B) > 0), "
                f"(A.astype(float32) - B) / (A.astype(float32) + B), {NODATA})")
        run(cmd + [f"--calc={expr}", f"--outfile={raw}", "--co=COMPRESS=DEFLATE", "--co=TILED=YES"], cwd=work)

        cog = out / f"{index['id']}.tif"
        to_cog(raw, cog, target_crs, float_data=True)
        files.append({"path": cog.name, "kind": f"{index['id']}_cog"})

        colors = work / f"{index['id']}_colors.txt"
        _color_file(index, colors)
        rgba = work / f"{index['id']}_rgba.tif"
        run(["gdaldem", "color-relief", "-q", "-alpha", str(raw), str(colors), str(rgba)], cwd=work)
        tiles = f"{index['id']}_tiles"
        run(["gdal2tiles", "--xyz", f"--zoom={minzoom}-{maxzoom}", "--webviewer=none", "--resampling=average",
             f"--processes={max(1, processes)}", "--tiledriver=PNG", str(rgba), str(out / tiles)], cwd=out)

        layers.append({
            "path": tiles, "format": "png", "minzoom": minzoom, "maxzoom": maxzoom,
            "kind": index["id"], "name": index["name"], "relative": True,
            "legend": {"range": index["range"], "palette": index["palette"], "unit": "",
                       "description": index.get("description", "")},
            "stats": _stats(raw),
        })
    return files, layers
