"""Thumbnails of media uploaded by Pilot 2, generated on first request and cached on disk.

JPEG (incl. DJI R-JPEG thermal): reduced-size DCT decode (Image.draft), fast on the Pi.
TIFF (Mavic 3M multispectral bands, 16 bit): grey preview, 2..98 percentile contrast stretch.
Videos get no thumbnail (the web shows an icon).
"""
import io
import os
import threading
from pathlib import Path

from PIL import Image, ImageOps

SIZE = 320
IMAGE_EXT = (".jpg", ".jpeg", ".tif", ".tiff")
_LIMIT = threading.BoundedSemaphore(2)       # at most two decodes at a time on the Pi


def supported(file_name: str) -> bool:
    return file_name.lower().endswith(IMAGE_EXT)


def cache_path(cache_dir: str, file_id: str) -> Path:
    safe = "".join(c for c in file_id if c.isalnum() or c == "-")
    return Path(cache_dir) / f"{safe}-{SIZE}.jpg"


def render(data: bytes) -> bytes:
    with _LIMIT:
        img = Image.open(io.BytesIO(data))
        if img.format == "JPEG":
            img.draft("RGB", (SIZE * 2, SIZE * 2))
        img = ImageOps.exif_transpose(img)
        if img.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
            # 16-bit band: stretch the 2nd..98th percentile (outliers would leave the preview black)
            img = img.convert("I") if img.mode != "F" else img
            img.thumbnail((SIZE, SIZE))
            values = sorted(img.getdata())
            lo, hi = values[len(values) * 2 // 100], values[len(values) * 98 // 100]
            scale = 255.0 / (hi - lo) if hi > lo else 1.0
            img = img.point(lambda v: (v - lo) * scale).convert("L")
        elif img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        img.thumbnail((SIZE, SIZE))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=80, optimize=True)
        return out.getvalue()


def get_or_create(cache_dir: str, file_id: str, load) -> bytes:
    """load() returns the original bytes; only called on a cache miss."""
    path = cache_path(cache_dir, file_id)
    if path.exists():
        return path.read_bytes()
    thumb = render(load())
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}{threading.get_ident()}")
    tmp.write_bytes(thumb)
    tmp.replace(path)
    return thumb
