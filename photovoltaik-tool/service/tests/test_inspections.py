"""Inspection flow end to end (pv/inspections.py, pv/analysis.py) with storage in /vsimem/.

The DJI Thermal SDK is replaced by a converter that returns known temperatures: two overlapping nadir
images 2 m apart (30 m altitude) see the same hot cell -> one merged anomaly; image 2 has a second one.
"""
import csv
import io
import json
import os
import struct
import unittest
import uuid
from xml.etree import ElementTree as ET

os.environ.setdefault("JWT_SECRET", "test-secret")
for _n in ("AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
    os.environ.setdefault(_n, "unused")

import numpy as np                                    # noqa: E402
from osgeo import gdal                                # noqa: E402

from pv import inspections, rjpeg, storage            # noqa: E402
from pv.server import HttpError                       # noqa: E402
from pv.thermal import ThermalError                   # noqa: E402
from tests.test_geo_detect import XMP, thermal_scene  # noqa: E402

USER = {"workspace_id": "ws-1", "username": "test"}
OTHER = {"workspace_id": "ws-2", "username": "intruder"}
LAT1, LON = 51.16, 10.45
LAT2 = LAT1 + 2.0 / 111_320.0                          # 2 m further north
GSD = 30.0 / (9.1 / 0.012)                             # m per raw pixel at 30 m


def real_rjpeg(lat: float) -> bytes:
    """Colour JPEG 1280 x 1024 made by GDAL, with EXIF/XMP/APP3 segments of a DJI R-JPEG spliced in."""
    name = f"/vsimem/{uuid.uuid4()}.jpg"
    mem = gdal.GetDriverByName("MEM").Create("", 1280, 1024, 3, gdal.GDT_Byte)
    for i in range(3):
        mem.GetRasterBand(i + 1).Fill(60 * (i + 1))
    gdal.GetDriverByName("JPEG").CreateCopy(name, mem)
    jpeg = storage.read(name)
    gdal.Unlink(name)
    from tests.test_geo_detect import fake_rjpeg
    xmp = dict(XMP, GpsLatitude=f"{lat:.9f}", GimbalYawDegree="0.0", LRFTargetDistance="0.000",
               LRFTargetLat="0", LRFTargetLon="0")
    header = fake_rjpeg(xmp)[2:-2]                     # segments without SOI and the fake SOS marker
    return jpeg[:2] + header + jpeg[2:]


def converter(jpeg: bytes, tp, distance: float):
    shot = rjpeg.read_shot(jpeg)
    t = thermal_scene(seed=3)
    if abs(shot.lat - LAT1) < 1e-9:
        t[254:258, 318:322] += 22.0                    # hot cell below the drone
    else:
        dy = int(round(2.0 / GSD))                     # same cell, seen 2 m further south in the image
        t[254 + dy:258 + dy, 318:322] += 22.0
        t[100:103, 500:503] += 8.0                     # a second, weaker one only here
    return t


class Request:
    def __init__(self, body=None, query=None):
        self._body, self.query, self.token = body or {}, query or {}, "token"

    def json(self):
        return self._body


class InspectionFlowTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = f"/vsimem/pv-test-{uuid.uuid4()}"
        cls.files = {"f1": real_rjpeg(LAT1), "f2": real_rjpeg(LAT2), "f3": b"\xff\xd8\xff\xd9 not thermal"}
        for fid, data in cls.files.items():
            storage.write(f"{cls.root}/media/{fid}.JPG", data)
        cls._orig = (inspections._prefix, inspections.media_files, inspections._media_path, inspections.converter)
        inspections._prefix = lambda: f"{cls.root}/inspections"
        inspections.media_files = lambda token, ids: [
            {"file_id": i, "file_name": f"DJI_{i}_T.JPG", "object_key": f"{i}.JPG"} for i in ids]
        inspections._media_path = lambda f: f"{cls.root}/media/{f['object_key']}"
        inspections.converter = converter

    @classmethod
    def tearDownClass(cls):
        (inspections._prefix, inspections.media_files, inspections._media_path, inspections.converter) = cls._orig
        gdal.RmdirRecursive(cls.root)

    def run_inspection(self, **body) -> str:
        status, meta = inspections.create(Request(dict({"file_ids": ["f1", "f2", "f3"], "name": "Testanlage"},
                                                       **body)), USER)
        self.assertEqual(status, 201)
        self.assertNotIn("workspace_id", meta)
        self.assertNotIn("files", meta)                 # object keys stay on the server
        inspections._jobs.get_nowait()                   # processed synchronously here instead of the worker
        inspections.process(meta["id"])
        return meta["id"]

    def test_flow_merge_and_classes(self):
        iid = self.run_inspection()
        r = inspections.get_inspection(Request(), USER, iid)
        self.assertEqual(r["status"], "done")
        self.assertEqual(r["summary"]["images"], 3)
        self.assertEqual(r["summary"]["analysed"], 2)
        skipped = [i for i in r["images"] if "skipped" in i]
        self.assertEqual([i["file_id"] for i in skipped], ["f3"])
        a = r["anomalies"]
        self.assertEqual(len(a), 2)                        # hot cell seen twice -> merged
        hot, warm = a
        self.assertEqual((hot["number"], hot["klass"]), (1, 3))
        self.assertEqual(hot["seen_in"], ["DJI_f1_T.JPG", "DJI_f2_T.JPG"])
        self.assertAlmostEqual(hot["lat"], LAT1, delta=0.3 / 111_320)     # under the first drone position
        self.assertAlmostEqual(hot["lon"], LON, delta=0.3 / 70_000)
        self.assertEqual(hot["type"], "Hotspot (Zelle/Punkt)")
        self.assertEqual(warm["klass"], 1)
        self.assertEqual(warm["seen_in"], ["DJI_f2_T.JPG"])
        png = inspections.crop(Request(), USER, iid, hot["id"])[1]
        self.assertTrue(png.startswith(b"\x89PNG"))

    def test_status_note_and_exports(self):
        iid = self.run_inspection()
        a = inspections.get_inspection(Request(), USER, iid)["anomalies"]
        inspections.set_status(Request({"status": "confirmed", "note": "Zelle gebrochen"}), USER, iid, a[0]["id"])
        inspections.set_status(Request({"status": "dismissed"}), USER, iid, a[1]["id"])
        with self.assertRaises(HttpError):
            inspections.set_status(Request({"status": "deleted"}), USER, iid, a[0]["id"])

        _, data, ctype, headers = inspections.export(Request(), USER, iid, "geojson")
        feats = json.loads(data)["features"]
        self.assertEqual(len(feats), 1)                    # dismissed one is not reported
        self.assertEqual(feats[0]["properties"]["note"], "Zelle gebrochen")
        lon, lat = feats[0]["geometry"]["coordinates"]
        self.assertAlmostEqual(lon, LON, delta=0.001)      # GeoJSON lon, lat
        _, data, _, _ = inspections.export(Request(query={"dismissed": "1"}), USER, iid, "geojson")
        self.assertEqual(len(json.loads(data)["features"]), 2)

        _, data, ctype, headers = inspections.export(Request(), USER, iid, "csv")
        rows = list(csv.reader(io.StringIO(data.decode("utf-8-sig")), delimiter=";"))
        self.assertEqual(rows[0][:3], ["number", "klass", "status"])
        self.assertEqual(rows[1][2], "confirmed")
        self.assertIn('filename="Testanlage.csv"', headers["Content-Disposition"])

        _, data, ctype, headers = inspections.export(Request(), USER, iid, "report")
        self.assertTrue(ctype.startswith("text/html"))
        self.assertIn(b"data:image/png;base64,", data)
        self.assertIn(b"Klasse 3", data)
        self.assertIn(b"Zelle gebrochen", data)

    def test_other_workspace(self):
        iid = self.run_inspection()
        for call in (lambda: inspections.get_inspection(Request(), OTHER, iid),
                     lambda: inspections.export(Request(), OTHER, iid, "csv"),
                     lambda: inspections.delete(Request(), OTHER, iid)):
            with self.assertRaises(HttpError) as ctx:
                call()
            self.assertEqual(ctx.exception.status, 404)
        self.assertNotIn(iid, [m["id"] for m in inspections.list_inspections(Request(), OTHER)])
        self.assertIn(iid, [m["id"] for m in inspections.list_inspections(Request(), USER)])

    def test_validation(self):
        for body in ({"file_ids": []}, {"thermal": {"emissivity": 2}}, {"detect": {"class1": 12, "class2": 10}},
                     {"merge_m": 50}):
            with self.subTest(body), self.assertRaises(HttpError) as ctx:
                inspections.create(Request(dict({"file_ids": ["f1"]}, **body)), USER)
            self.assertEqual(ctx.exception.status, 422)

    def test_sdk_missing_fails_the_inspection(self):
        def broken(*args):
            raise ThermalError("DJI Thermal SDK nicht installiert")
        inspections.converter = broken
        try:
            iid = self.run_inspection()
        finally:
            inspections.converter = converter
        meta = inspections.get_inspection(Request(), USER, iid)
        self.assertEqual(meta["status"], "failed")
        self.assertIn("SDK", meta["error"])
        with self.assertRaises(HttpError) as ctx:
            inspections.export(Request(), USER, iid, "csv")
        self.assertEqual(ctx.exception.status, 409)

    def test_delete_and_unknown(self):
        iid = self.run_inspection()
        inspections.delete(Request(), USER, iid)
        with self.assertRaises(HttpError) as ctx:
            inspections.get_inspection(Request(), USER, iid)
        self.assertEqual(ctx.exception.status, 404)


if __name__ == "__main__":
    unittest.main()
