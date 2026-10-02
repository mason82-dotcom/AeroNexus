"""Object storage through GDAL's virtual file systems (/vsis3/ = MinIO; tests use /vsimem/)."""
from osgeo import gdal

gdal.UseExceptions()


class NotFound(Exception):
    pass


def write(path: str, data: bytes) -> None:
    f = gdal.VSIFOpenL(path, "wb")
    try:
        gdal.VSIFWriteL(data, 1, len(data), f)
    finally:
        gdal.VSIFCloseL(f)


def read(path: str) -> bytes:
    try:
        f = gdal.VSIFOpenL(path, "rb")
    except RuntimeError as exc:
        raise NotFound(path) from exc
    if f is None:                       # missing object: GDAL returns NULL instead of raising
        raise NotFound(path)
    try:
        gdal.VSIFSeekL(f, 0, 2)
        size = gdal.VSIFTellL(f)
        gdal.VSIFSeekL(f, 0, 0)
        return gdal.VSIFReadL(1, size, f)
    finally:
        gdal.VSIFCloseL(f)


def list_dirs(prefix: str) -> list[str]:
    return [d.rstrip("/") for d in (gdal.ReadDir(prefix) or [])]


def remove_tree(prefix: str) -> None:
    gdal.RmdirRecursive(prefix)
