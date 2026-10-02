"""ISO 11783-10 (ISOBUS) TASKDATA export of a zone map: grid type 1 + treatment zones.

The class raster (EPSG:25832, values 0 = outside, 1..n = zone) is resampled to a WGS84 grid of the same
ground cell size. GRD00001.BIN holds one byte per cell = treatment zone code, rows from the south-west
corner northwards, columns west to east. Each TZN carries the rate as process data value of the given DDI
(0006 mass per area in mg/m2, 0001 volume per area in mm3/m2; both = rate per ha * 100).
Experimental: verify on the target terminal before field use.
"""
import io
import math
import os
import tempfile
import zipfile
from xml.sax.saxutils import quoteattr

import numpy as np
from osgeo import gdal

gdal.UseExceptions()


def build(meta: dict, class_raster: bytes, ddi: str) -> bytes:
    with tempfile.TemporaryDirectory() as tmp:
        src = os.path.join(tmp, "classes.tif")
        with open(src, "wb") as f:
            f.write(class_raster)
        info = gdal.Info(src, format="json")
        lat = info["wgs84Extent"]["coordinates"][0][0][1]
        cell_m = float(meta["params"]["cell_m"])
        cell_lat = cell_m / 111320.0
        cell_lon = cell_m / (111320.0 * math.cos(math.radians(lat)))
        ds = gdal.Warp("", src, format="MEM", dstSRS="EPSG:4326", xRes=cell_lon, yRes=cell_lat,
                       resampleAlg="near", srcNodata=0, dstNodata=0)
        grid = ds.GetRasterBand(1).ReadAsArray().astype(np.uint8)
        gt = ds.GetGeoTransform()
    rows, cols = grid.shape
    west, north = gt[0], gt[3]
    south = north + rows * gt[5]
    cells = np.flipud(grid).tobytes()           # first row = southernmost
    area_m2 = int(round(sum(z["area_ha"] for z in meta["zones"]) * 10000))

    def a(value) -> str:
        return quoteattr(str(value))

    product = meta.get("product") or "Product"
    tzn = ['<TZN A="0" B="Outside field"><PDV A=' + a(ddi) + ' B="0" C="PDT1"/></TZN>']
    for z in meta["zones"]:
        value = int(round(float(z["rate"]) * 100))
        tzn.append(f'<TZN A="{z["zone"]}" B={a("Zone " + str(z["zone"]))}><PDV A={a(ddi)} B="{value}" C="PDT1"/></TZN>')
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<ISO11783_TaskData VersionMajor="4" VersionMinor="3" ManagementSoftwareManufacturer="AeroNexus" '
        'ManagementSoftwareVersion="1.0" DataTransferOrigin="1">\n'
        f'<PDT A="PDT1" B={a(product)}/>\n'
        f'<PFD A="PFD1" C={a(meta["name"][:32])} D="{area_m2}"/>\n'
        f'<TSK A="TSK1" B={a(meta["name"][:32])} E="PFD1" G="1" H="0" I="0" J="0">\n'
        + "\n".join(tzn) + "\n"
        f'<GRD A="{south:.9f}" B="{west:.9f}" C="{cell_lat:.9f}" D="{cell_lon:.9f}" E="{cols}" F="{rows}" '
        f'G="GRD00001" H="{cols * rows}" I="1"/>\n'
        "</TSK>\n</ISO11783_TaskData>\n")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("TASKDATA/TASKDATA.XML", xml)
        zf.writestr("TASKDATA/GRD00001.BIN", cells)
    return buf.getvalue()
