"""Thermal anomalies (hot spots) in one temperature image.

Candidates: pixels at least `min_delta` K above the background.
Background = robust local reference temperature: 75th percentile of 16 x 16 pixel blocks (the module
temperature, also where a block is half cool row gap), median over the neighbouring blocks, interpolated
back to full size. Long thin areas across more than `max_extent` of the image (row gaps, roof edges) and
large areas (`max_fraction`, warm roofs/ground) are not reported; inside them only spots that are clearly
warmer than the area itself count (hot cell next to a warm row gap). Delta-T of a spot = its maximum minus
the 75th percentile of a 4 px ring around it, so edges of warm areas drop out. Connected areas use 8-connectivity
(labelled with GDAL Polygonize/Rasterize).
Classes follow the usual practice for IEC TS 62446-3 reports (thresholds configurable):
  1 = beobachten (>= class1 K), 2 = Wartung planen (>= class2 K), 3 = dringend (>= class3 K)
"""
from dataclasses import dataclass, field

import numpy as np
from osgeo import gdal, ogr

gdal.UseExceptions()
ogr.UseExceptions()
BLOCK = 16


@dataclass
class Params:
    min_delta: float = 3.0          # K above local background
    min_pixels: int = 2             # smaller spots are sensor noise
    max_fraction: float = 0.05      # larger warm areas (> 5 % of the image) are roofs/ground, not module faults
    max_extent: float = 0.5         # longer than half the image: row gap or edge, not a module fault
    class1: float = 3.0
    class2: float = 10.0
    class3: float = 20.0

    @classmethod
    def from_dict(cls, d: dict) -> "Params":
        known = {k: type(getattr(cls, k))(v) for k, v in (d or {}).items() if hasattr(cls, k)}
        return cls(**known)


@dataclass
class Spot:
    x: float                        # centroid, raw pixel grid
    y: float
    pixels: int
    bbox: tuple[int, int, int, int]  # x0, y0, x1, y1 (exclusive)
    t_max: float
    t_mean: float
    t_background: float
    delta: float                    # t_max - background
    klass: int
    extent_px: tuple[float, float] = field(default=(0.0, 0.0))   # long / short side of the bbox in px


def background(temp: np.ndarray) -> np.ndarray:
    h, w = temp.shape
    bh, bw = h // BLOCK, w // BLOCK
    blocks = np.percentile(temp[:bh * BLOCK, :bw * BLOCK].reshape(bh, BLOCK, bw, BLOCK).swapaxes(1, 2)
                           .reshape(bh, bw, BLOCK * BLOCK), 75, axis=2)
    # median over the 3 x 3 neighbouring blocks: a hot module does not lift its own reference
    padded = np.pad(blocks, 1, mode="edge")
    stack = np.stack([padded[dy:dy + bh, dx:dx + bw] for dy in range(3) for dx in range(3)])
    smooth = np.median(stack, axis=0)
    # bilinear interpolation of the block centres back to pixel resolution
    yc = (np.arange(bh) + 0.5) * BLOCK
    xc = (np.arange(bw) + 0.5) * BLOCK
    rows = np.array([np.interp(np.arange(w), xc, smooth[i]) for i in range(bh)])
    return np.array([np.interp(np.arange(h), yc, rows[:, j]) for j in range(w)]).T


def _labels(mask: np.ndarray) -> tuple[np.ndarray, int]:
    h, w = mask.shape
    drv = gdal.GetDriverByName("MEM")
    src = drv.Create("", w, h, 1, gdal.GDT_Byte)
    band = src.GetRasterBand(1)
    band.WriteArray(mask.astype(np.uint8))
    vds = ogr.GetDriverByName("MEM").CreateDataSource("spots")
    lyr = vds.CreateLayer("spots")
    lyr.CreateField(ogr.FieldDefn("v", ogr.OFTInteger))
    lyr.CreateField(ogr.FieldDefn("id", ogr.OFTInteger))
    gdal.Polygonize(band, band, lyr, 0, ["8CONNECTED=8"])
    n = 0
    for feat in lyr:
        n += 1
        feat.SetField("id", n)
        lyr.SetFeature(feat)
    out = drv.Create("", w, h, 1, gdal.GDT_UInt32)
    gdal.RasterizeLayer(out, [1], lyr, options=["ATTRIBUTE=id"])
    return out.GetRasterBand(1).ReadAsArray(), n


def classify(delta: float, p: Params) -> int:
    return 3 if delta >= p.class3 else 2 if delta >= p.class2 else 1 if delta >= p.class1 else 0


RING = 4                            # px around a spot used as its local reference


def _ring_reference(temp: np.ndarray, comp: np.ndarray, bbox: tuple[int, int, int, int]) -> float | None:
    """75th percentile of the pixels around the spot (outside it): the local module temperature."""
    x0, y0, x1, y1 = bbox
    h, w = temp.shape
    ya, yb, xa, xb = max(0, y0 - RING), min(h, y1 + RING), max(0, x0 - RING), min(w, x1 + RING)
    ring = temp[ya:yb, xa:xb][~comp[ya:yb, xa:xb]]
    return float(np.percentile(ring, 75)) if ring.size >= 8 else None


def _components(mask: np.ndarray):
    labels, n = _labels(mask)
    for i in range(1, n + 1):
        comp = labels == i
        ys, xs = np.nonzero(comp)
        yield comp, ys, xs


def find_spots(temp: np.ndarray, p: Params | None = None) -> list[Spot]:
    p = p or Params()
    h, w = temp.shape
    bg = background(temp)
    candidates = []
    for comp, ys, xs in _components((temp - bg) >= p.min_delta):
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        too_big = len(xs) > p.max_fraction * temp.size
        too_long = (x1 - x0) > p.max_extent * w or (y1 - y0) > p.max_extent * h
        if not (too_big or too_long):
            candidates.append((comp, ys, xs))
            continue
        # warm row gap / roof edge: look for clear hot spots inside it, relative to the area itself
        level = float(np.percentile(temp[ys, xs], 75))
        inner = np.zeros_like(comp)
        inner[ys, xs] = temp[ys, xs] - level >= p.min_delta
        for sub in _components(inner):
            sub_x = sub[2]
            if len(sub_x) <= p.max_fraction * temp.size:
                candidates.append(sub)

    spots = []
    for comp, ys, xs in candidates:
        if len(xs) < p.min_pixels:
            continue
        x0, x1, y0, y1 = int(xs.min()), int(xs.max()) + 1, int(ys.min()), int(ys.max()) + 1
        if (x1 - x0) > p.max_extent * w or (y1 - y0) > p.max_extent * h:
            continue
        vals = temp[ys, xs]
        k = int(np.argmax(vals))
        ref = _ring_reference(temp, comp, (x0, y0, x1, y1))
        if ref is None:
            continue
        delta = float(vals[k]) - ref
        klass = classify(delta, p)
        if delta < p.min_delta or klass == 0:      # edge of a larger warm area: not warmer than its ring
            continue
        spots.append(Spot(x=float(xs.mean()), y=float(ys.mean()), pixels=int(len(xs)), bbox=(x0, y0, x1, y1),
                          t_max=round(float(vals[k]), 2), t_mean=round(float(vals.mean()), 2),
                          t_background=round(ref, 2), delta=round(delta, 2), klass=klass,
                          extent_px=(float(max(x1 - x0, y1 - y0)), float(min(x1 - x0, y1 - y0)))))
    return sorted(spots, key=lambda s: -s.delta)


def guess_type(area_m2: float | None, long_m: float | None, short_m: float | None) -> str:
    """Rough fault type from size (typical module 1.7 x 1.1 m, cell 0.16 m). Shown as estimate only."""
    if area_m2 is None or long_m is None or short_m is None:
        return "unbekannt"
    if area_m2 < 0.06:
        return "Hotspot (Zelle/Punkt)"
    if long_m > 2.5 * max(short_m, 0.01) and area_m2 < 1.0:
        return "Streifen (Substring/Bypass-Diode)"
    if area_m2 < 2.6:
        return "Modul"
    return "Mehrere Module/String"
