"""Visible sibling photos (pv/inspections.visible_sibling) and the thermal -> visible crop (pv/analysis)."""
import os
import struct
import unittest
import uuid

os.environ.setdefault("JWT_SECRET", "test-secret")
for _n in ("AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
    os.environ.setdefault(_n, "unused")

import numpy as np                                   # noqa: E402
from osgeo import gdal                               # noqa: E402

from pv import analysis, inspections, rjpeg, storage  # noqa: E402


def jpeg_with_focal35(w: int, h: int, f35: int, marker: tuple[int, int] | None = None) -> bytes:
    """Grey JPEG of w x h with EXIF FocalLengthIn35mmFilm; optional white 20 px square at marker (x, y)."""
    name = f"/vsimem/{uuid.uuid4()}.jpg"
    img = np.full((h, w), 90, dtype=np.uint8)
    if marker:
        x, y = marker
        img[y - 10:y + 10, x - 10:x + 10] = 255
    mem = gdal.GetDriverByName("MEM").Create("", w, h, 3, gdal.GDT_Byte)
    for i in range(3):
        mem.GetRasterBand(i + 1).WriteArray(img)
    gdal.GetDriverByName("JPEG").CreateCopy(name, mem)
    data = storage.read(name)
    gdal.Unlink(name)
    tiff = b"II*\x00" + struct.pack("<I", 8)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, 26) + struct.pack("<I", 0)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0xA405, 3, 1, f35) + struct.pack("<I", 0)
    app1 = b"Exif\x00\x00" + tiff
    seg = b"\xff\xe1" + struct.pack(">H", len(app1) + 2) + app1
    return data[:2] + seg + data[2:]


def png_array(data: bytes) -> np.ndarray:
    name = f"/vsimem/{uuid.uuid4()}.png"
    gdal.FileFromMemBuffer(name, data)
    ds = gdal.Open(name)
    arr = ds.GetRasterBand(1).ReadAsArray()
    ds = None
    gdal.Unlink(name)
    return arr


class SiblingTest(unittest.TestCase):
    def setUp(self):
        names = ["DJI_20261002154339_0002_V.JPG", "DJI_20261001224911_0003_V.JPG", "DJI_20261001224906_0002_W.JPG",
                 "DJI_20261001224906_0009_V.JPG"]
        self.by_name = {n.upper(): {"file_name": n} for n in names}

    def test_same_stamp(self):
        self.assertEqual(inspections.visible_sibling("DJI_20261002154339_0002_T.JPG", self.by_name)["file_name"],
                         "DJI_20261002154339_0002_V.JPG")

    def test_one_second_later_and_w_suffix(self):
        self.assertEqual(inspections.visible_sibling("DJI_20261001224910_0003_T.JPG", self.by_name)["file_name"],
                         "DJI_20261001224911_0003_V.JPG")
        self.assertEqual(inspections.visible_sibling("DJI_20261001224906_0002_T.JPG", self.by_name)["file_name"],
                         "DJI_20261001224906_0002_W.JPG")

    def test_index_must_match_and_unknown_names(self):
        self.assertIsNone(inspections.visible_sibling("DJI_20261001224906_0005_T.JPG", self.by_name))
        self.assertIsNone(inspections.visible_sibling("IMG_1234.JPG", self.by_name))


class VisibleCropTest(unittest.TestCase):
    def test_exif_focal35(self):
        self.assertEqual(rjpeg.exif_focal(jpeg_with_focal35(64, 48, 24))[1], 24)

    def test_spot_maps_into_the_wide_photo(self):
        # thermal f35 40 (M3T), wide f35 24, 4000 x 3000: raw (480, 384) -> visible
        w, h, ft, fv = 4000, 3000, 40, 24
        k = (fv * np.hypot(w, h)) / (ft * np.hypot(640, 512))
        tx, ty = 480, 384
        vx, vy = int(w / 2 + (tx - 320) * k), int(h / 2 + (ty - 256) * k)
        vis = jpeg_with_focal35(w, h, fv, marker=(vx, vy))
        png = analysis.visible_crop(vis, ft, (tx - 2, ty - 2, tx + 2, ty + 2))
        self.assertIsNotNone(png)
        arr = png_array(png)
        bright = np.argwhere(arr > 240)
        cy, cx = bright.mean(axis=0)
        # the white marker lies in the middle of the crop (inside the frame)
        self.assertAlmostEqual(cx / arr.shape[1], 0.5, delta=0.08)
        self.assertAlmostEqual(cy / arr.shape[0], 0.5, delta=0.08)
        self.assertLessEqual(arr.shape[1], analysis.VISIBLE_MAX_PX)

    def test_spot_outside_a_zoom_photo(self):
        vis = jpeg_with_focal35(4000, 3000, 161)                     # zoom: narrower than the thermal camera
        self.assertIsNone(analysis.visible_crop(vis, 40, (10, 10, 14, 14)))      # thermal corner -> outside
        self.assertIsNotNone(analysis.visible_crop(vis, 40, (318, 254, 322, 258)))  # centre -> inside

    def test_missing_focal_length(self):
        self.assertIsNone(analysis.visible_crop(jpeg_with_focal35(64, 48, 24), None, (1, 1, 3, 3)))


if __name__ == "__main__":
    unittest.main()
