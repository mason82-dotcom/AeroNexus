"""Thermal pre-measurement and matrix cache (pv/premeasure.py) with storage in /vsimem/."""
import os
import unittest
import uuid

os.environ.setdefault("JWT_SECRET", "test-secret")
for _n in ("AWS_S3_ENDPOINT", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"):
    os.environ.setdefault(_n, "unused")

import numpy as np                                    # noqa: E402

from pv import analysis, premeasure, rjpeg, storage   # noqa: E402
from pv.thermal import ThermalParams                  # noqa: E402
from tests.test_inspections import LAT1, real_rjpeg   # noqa: E402


class CountingSdk:
    def __init__(self):
        self.calls = []

    def __call__(self, jpeg, tp, distance):
        self.calls.append((tp.emissivity, round(distance, 1)))
        return np.full((rjpeg.RAW_H, rjpeg.RAW_W), 21.5, dtype="<f4")


class PremeasureTest(unittest.TestCase):
    def setUp(self):
        root = f"/vsimem/{uuid.uuid4()}"
        self.media, self.cache = f"{root}/media", f"{root}/cache"
        self.sdk = CountingSdk()
        self.convert = premeasure.cached_converter(self.sdk, self.cache)
        self.jpeg = real_rjpeg(LAT1)

    def test_cache_hit_for_same_image_and_parameters(self):
        a = self.convert(self.jpeg, ThermalParams(), 30.0)
        b = self.convert(self.jpeg, ThermalParams(), 30.04)        # same 0.1 m bucket
        self.assertEqual(len(self.sdk.calls), 1)
        np.testing.assert_array_equal(a, b)
        self.assertEqual(b.shape, (rjpeg.RAW_H, rjpeg.RAW_W))

    def test_other_parameters_measure_again(self):
        self.convert(self.jpeg, ThermalParams(), 30.0)
        self.convert(self.jpeg, ThermalParams(emissivity=0.95), 30.0)
        self.convert(self.jpeg, ThermalParams(), 12.0)
        self.assertEqual(len(self.sdk.calls), 3)

    def test_pending_thermal_images_are_measured_once(self):
        storage.write(f"{self.media}/wayline/DJI_0001_T.JPG", self.jpeg)
        storage.write(f"{self.media}/wayline/DJI_0001_V.JPG", b"visible")
        storage.write(f"{self.media}/wayline/DJI_0002_T.JPG", b"not an r-jpeg")
        self.assertEqual(premeasure.thermal_keys(self.media),
                         ["wayline/DJI_0001_T.JPG", "wayline/DJI_0002_T.JPG"])
        self.assertEqual(premeasure.premeasure_pending(self.media, self.cache, self.convert), 1)
        self.assertEqual(premeasure.premeasure_pending(self.media, self.cache, self.convert), 0)
        self.assertEqual(len(self.sdk.calls), 1)

    def test_inspection_with_defaults_reuses_the_premeasured_matrix(self):
        storage.write(f"{self.media}/wayline/DJI_0001_T.JPG", self.jpeg)
        premeasure.premeasure_pending(self.media, self.cache, self.convert)
        # what analysis.analyze_image does for an inspection with default parameters
        self.convert(self.jpeg, ThermalParams(), analysis.target_distance(rjpeg.read_shot(self.jpeg)))
        self.assertEqual(len(self.sdk.calls), 1)


if __name__ == "__main__":
    unittest.main()
