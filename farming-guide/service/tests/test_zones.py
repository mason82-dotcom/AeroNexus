"""Zone maps, rates and all four exports end to end (farming/zones.py, farming/isoxml.py).

Storage is redirected from MinIO (/vsis3/) to GDAL's in-memory file system, the index layer is a synthetic
NDVI raster: 300 x 200 m field, NDVI rising from west (0.2) to east (0.8), so zone 1 must be in the west.
"""
import io
import json
import os
import unittest
import uuid
import zipfile
from xml.etree import ElementTree as ET

os.environ.setdefault("JWT_SECRET", "test-secret")
for _name in ("AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
    os.environ.setdefault(_name, "unused")

import numpy as np                                           # noqa: E402
from osgeo import gdal, ogr, osr                              # noqa: E402

from farming import isoxml, zones                             # noqa: E402
from farming.server import HttpError                          # noqa: E402

BASE_LON, BASE_LAT = 10.45, 51.16                             # generic test site (edge/.env.example)
USER = {"workspace_id": "ws-1", "username": "test"}
OTHER = {"workspace_id": "ws-2", "username": "intruder"}


def utm_origin() -> tuple[float, float]:
    wgs, utm = osr.SpatialReference(), osr.SpatialReference()
    wgs.ImportFromEPSG(4326)
    wgs.SetAxisMappingStrategy(osr.OAMS_TRADITIONAL_GIS_ORDER)
    utm.ImportFromEPSG(25832)
    x, y, _ = osr.CoordinateTransformation(wgs, utm).TransformPoint(BASE_LON, BASE_LAT)
    return round(x), round(y)


def ndvi_raster(path: str, width_m: int = 300, height_m: int = 200, border_px: int = 10) -> None:
    """1 m NDVI raster in EPSG:25832, west-east gradient, nodata border around the field."""
    x0, y0 = utm_origin()
    cols, rows = width_m + 2 * border_px, height_m + 2 * border_px
    ds = gdal.GetDriverByName("GTiff").Create(path, cols, rows, 1, gdal.GDT_Float32)
    ds.SetGeoTransform((x0 - border_px, 1, 0, y0 + height_m + border_px, 0, -1))
    srs = osr.SpatialReference()
    srs.ImportFromEPSG(25832)
    ds.SetProjection(srs.ExportToWkt())
    data = np.full((rows, cols), zones.NODATA, dtype=np.float32)
    data[border_px:-border_px, border_px:-border_px] = np.linspace(0.2, 0.8, width_m, dtype=np.float32)[None, :]
    band = ds.GetRasterBand(1)
    band.SetNoDataValue(zones.NODATA)
    band.WriteArray(data)
    ds = None


class Request:
    def __init__(self, body=None, query=None):
        self._body, self.query, self.token = body or {}, query or {}, "token"

    def json(self):
        return self._body


class ZoneMapFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = f"/vsimem/test-{uuid.uuid4()}"
        cls.ndvi = f"{cls.root}/ndvi.tif"
        ndvi_raster(cls.ndvi)
        cls._orig = (zones._prefix, zones._index_path, zones._layer)
        zones._prefix = lambda: f"{cls.root}/zonemaps"
        zones._index_path = lambda layer: cls.ndvi
        zones._layer = lambda user, token, layer_id: {
            "id": layer_id, "job_id": "job-1", "kind": "ndvi", "name": "NDVI test", "relative": True}

    @classmethod
    def tearDownClass(cls):
        zones._prefix, zones._index_path, zones._layer = cls._orig
        gdal.RmdirRecursive(cls.root)

    def create(self, **body) -> dict:
        status, meta = zones.create_zonemap(Request(dict({"layer_id": "layer-1", "classes": 3,
                                                          "method": "quantile", "cell_m": 5,
                                                          "min_area_m2": 0}, **body)), USER)
        self.assertEqual(status, 201)
        return meta

    def test_quantile_zones_west_to_east(self):
        meta = self.create()
        self.assertNotIn("workspace_id", meta)                         # never leaked to the client
        # 300 m x 200 m = 6 ha; boundary cells that are only partly in the field count fully,
        # so up to one cell (5 m) around the 1000 m perimeter is added: 6.0 .. 6.5 ha
        self.assertGreaterEqual(meta["valid_ha"], 6.0)
        self.assertLessEqual(meta["valid_ha"], 6.5)
        z1, z2, z3 = meta["zones"]
        for z in (z1, z2, z3):                                         # equal area = 2 ha each
            self.assertAlmostEqual(z["area_ha"], meta["valid_ha"] / 3, delta=0.15)
        self.assertLess(z1["idx_mean"], z2["idx_mean"])
        self.assertLess(z2["idx_mean"], z3["idx_mean"])
        self.assertEqual([z1["color"], z3["color"]], ["#d73027", "#1a9850"])   # low = red, high = green

        fc = json.loads(zones._read(zones._key(meta["id"], "zones.geojson")))
        centroids = {}
        for f in fc["features"]:
            geom = ogr.CreateGeometryFromJson(json.dumps(f["geometry"]))
            c = geom.Centroid()
            # GeoJSON order is lon, lat: the field lies next to (10.45, 51.16), not at (51.16, 10.45)
            self.assertAlmostEqual(c.GetX(), BASE_LON + 0.002, delta=0.01)
            self.assertAlmostEqual(c.GetY(), BASE_LAT + 0.001, delta=0.01)
            centroids.setdefault(f["properties"]["zone"], []).append(c.GetX())
        self.assertLess(max(centroids[1]), min(centroids[3]))          # zone 1 west of zone 3
        total = sum(f["properties"]["area_ha"] for f in fc["features"])
        self.assertAlmostEqual(total, meta["valid_ha"], delta=0.1)

    def test_equal_interval_limits(self):
        meta = self.create(method="equal", classes=4)
        limits = meta["limits"]
        self.assertEqual(len(limits), 5)
        steps = np.diff(limits)
        self.assertTrue(np.allclose(steps, steps[0], atol=1e-3))

    def test_invalid_parameters(self):
        for body in ({"classes": 2}, {"classes": 6}, {"method": "kmeans"}, {"cell_m": 0.5}, {"cell_m": 51},
                     {"min_area_m2": -1}):
            with self.subTest(body), self.assertRaises(HttpError) as ctx:
                self.create(**body)
            self.assertEqual(ctx.exception.status, 422)

    def test_too_coarse_grid(self):
        small = f"{self.root}/small.tif"
        ndvi_raster(small, width_m=60, height_m=40)                     # 50 m cells: ~3 x 2 < 5 classes * 4
        zones._index_path = lambda layer: small
        try:
            with self.assertRaises(HttpError) as ctx:
                self.create(cell_m=50, classes=5)
            self.assertEqual(ctx.exception.status, 422)
        finally:
            zones._index_path = lambda layer: self.ndvi

    def test_rates_validation(self):
        zid = self.create()["id"]
        for body in ({"unit": "t/ha"}, {"rates": {"1": "abc"}}, {"rates": {"2": -1}}, {"rates": {"3": 100001}}):
            with self.subTest(body), self.assertRaises(HttpError) as ctx:
                zones.set_rates(Request(body), USER, zid)
            self.assertEqual(ctx.exception.status, 422)

    def test_other_workspace_cannot_see_or_change(self):
        zid = self.create()["id"]
        for call in (lambda: zones.get_zonemap(Request(), OTHER, zid),
                     lambda: zones.set_rates(Request({"rates": {"1": 1}}), OTHER, zid),
                     lambda: zones.export(Request(), OTHER, zid, "geojson"),
                     lambda: zones.delete_zonemap(Request(), OTHER, zid)):
            with self.assertRaises(HttpError) as ctx:
                call()
            self.assertEqual(ctx.exception.status, 404)
        self.assertNotIn(zid, [m["id"] for m in zones.list_zonemaps(Request(), OTHER)])
        self.assertIn(zid, [m["id"] for m in zones.list_zonemaps(Request(), USER)])

    def test_exports(self):
        zid = self.create()["id"]
        with self.assertRaises(HttpError) as ctx:                       # ISO-XML needs every rate
            zones.export(Request(), USER, zid, "isoxml")
        self.assertEqual(ctx.exception.status, 422)
        zones.set_rates(Request({"unit": "kg/ha", "product": "KAS 27",
                                 "rates": {"1": 180, "2": 150, "3": 120.5}}), USER, zid)

        status, data, ctype, headers = zones.export(Request(), USER, zid, "geojson")
        rates = {f["properties"]["zone"]: f["properties"]["rate"] for f in json.loads(data)["features"]}
        self.assertEqual(rates, {1: 180, 2: 150, 3: 120.5})
        self.assertIn("attachment", headers["Content-Disposition"])

        _, data, ctype, _ = zones.export(Request(), USER, zid, "shapefile")
        self.assertEqual(ctype, "application/zip")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            exts = {n.rsplit(".", 1)[1] for n in zf.namelist()}
            self.assertTrue({"shp", "shx", "dbf", "prj"} <= exts)
            gdal.FileFromMemBuffer(f"{self.root}/shp.zip", data)
        shp = ogr.Open(f"/vsizip/{self.root}/shp.zip")
        lyr = shp.GetLayer(0)
        self.assertEqual({f.GetField("ZONE"): f.GetField("RATE") for f in lyr}, {1: 180, 2: 150, 3: 120.5})
        self.assertEqual(lyr.GetSpatialRef().GetAuthorityCode(None), "4326")

        _, data, _, _ = zones.export(Request(), USER, zid, "kml")
        root = ET.fromstring(data)
        ns = {"k": "http://www.opengis.net/kml/2.2"}
        coords = root.find(".//k:coordinates", ns).text.split()[0].split(",")
        self.assertAlmostEqual(float(coords[0]), BASE_LON, delta=0.01)   # KML is lon,lat too
        self.assertAlmostEqual(float(coords[1]), BASE_LAT, delta=0.01)

        _, data, _, _ = zones.export(Request(), USER, zid, "isoxml")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = ET.fromstring(zf.read("TASKDATA/TASKDATA.XML"))
            cells = zf.read("TASKDATA/GRD00001.BIN")
        tzn = {int(t.get("A")): t.find("PDV") for t in xml.iter("TZN")}
        self.assertEqual({k: v.get("B") for k, v in tzn.items()}, {0: "0", 1: "18000", 2: "15000", 3: "12050"})
        self.assertTrue(all(v.get("A") == "0006" for v in tzn.values()))      # kg/ha -> DDI 0006
        self.assertEqual(xml.find(".//PDT").get("B"), "KAS 27")

        with self.assertRaises(HttpError) as ctx:
            zones.export(Request(), USER, zid, "pdf")
        self.assertEqual(ctx.exception.status, 404)

    def test_unknown_id_is_404(self):
        with self.assertRaises(HttpError) as ctx:          # was a 500 (VSIFOpenL returns None)
            zones.get_zonemap(Request(), USER, str(uuid.uuid4()))
        self.assertEqual(ctx.exception.status, 404)

    def test_delete(self):
        zid = self.create()["id"]
        zones.delete_zonemap(Request(), USER, zid)
        with self.assertRaises(HttpError):
            zones.get_zonemap(Request(), USER, zid)


class IsoxmlGridTest(unittest.TestCase):
    """GRD00001.BIN layout: rows from the south-west corner northwards, one byte per cell."""

    def test_grid_orientation_and_size(self):
        x0, y0 = utm_origin()
        path = f"/vsimem/classes-{uuid.uuid4()}.tif"
        ds = gdal.GetDriverByName("GTiff").Create(path, 20, 20, 1, gdal.GDT_Byte)   # 20 x 20 cells of 5 m
        ds.SetGeoTransform((x0, 5, 0, y0 + 100, 0, -5))
        srs = osr.SpatialReference()
        srs.ImportFromEPSG(25832)
        ds.SetProjection(srs.ExportToWkt())
        cls = np.full((20, 20), 2, dtype=np.uint8)       # north half zone 2
        cls[10:, :] = 1                                  # south half zone 1 (raster rows go north -> south)
        ds.GetRasterBand(1).WriteArray(cls)
        ds.GetRasterBand(1).SetNoDataValue(0)
        ds = None
        f = gdal.VSIFOpenL(path, "rb")
        gdal.VSIFSeekL(f, 0, 2)
        size = gdal.VSIFTellL(f)
        gdal.VSIFSeekL(f, 0, 0)
        raster = gdal.VSIFReadL(1, size, f)
        gdal.VSIFCloseL(f)
        gdal.Unlink(path)

        meta = {"name": "grid test", "params": {"cell_m": 5}, "product": "N",
                "zones": [{"zone": 1, "rate": 1.5, "area_ha": 0.5}, {"zone": 2, "rate": 2, "area_ha": 0.5}]}
        data = isoxml.build(meta, raster, "0001")
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            xml = ET.fromstring(zf.read("TASKDATA/TASKDATA.XML"))
            cells = zf.read("TASKDATA/GRD00001.BIN")
        grd = xml.find(".//GRD")
        cols, rows = int(grd.get("E")), int(grd.get("F"))
        self.assertEqual(len(cells), cols * rows)
        self.assertEqual(int(grd.get("H")), cols * rows)
        self.assertEqual(grd.get("I"), "1")
        self.assertAlmostEqual(float(grd.get("A")), BASE_LAT, delta=0.001)    # south edge
        self.assertAlmostEqual(float(grd.get("B")), BASE_LON, delta=0.001)    # west edge
        grid = np.frombuffer(cells, dtype=np.uint8).reshape(rows, cols)
        inner = grid[1:-1, 1:-1]                                               # resampling edge cells
        self.assertTrue(set(np.unique(grid)) <= {0, 1, 2})
        self.assertEqual(int(np.median(inner[: rows // 3])), 1)               # first rows = south = zone 1
        self.assertEqual(int(np.median(inner[-rows // 3:])), 2)
        pdv = {t.get("A"): t.find("PDV").get("B") for t in xml.iter("TZN")}
        self.assertEqual(pdv, {"0": "0", "1": "150", "2": "200"})               # rate * 100, l/ha -> DDI 0001
        self.assertTrue(all(t.find("PDV").get("A") == "0001" for t in xml.iter("TZN")))


if __name__ == "__main__":
    unittest.main()
