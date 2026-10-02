"""Hot spot detection (pv/detect.py), pixel -> ground projection (pv/geo.py), R-JPEG parsing (pv/rjpeg.py).

Optional: AERONEXUS_REAL_RJPEG_DIR=<dir with DJI *_T.JPG> also parses real M3T/M4T files
(never commit them: public repo, they contain the flight position).
"""
import math
import os
import struct
import unittest
from pathlib import Path

import numpy as np

from pv import detect, geo, rjpeg

BASE_LAT, BASE_LON = 51.16, 10.45          # generic test site (edge/.env.example)


def shot(**kw) -> rjpeg.Shot:
    base = dict(model="M3T", image_source="InfraredCamera", lat=BASE_LAT, lon=BASE_LON, gps_valid=True,
                abs_alt=330.0, rel_alt=30.0, gimbal_yaw=0.0, gimbal_pitch=-90.0, gimbal_roll=0.0,
                lrf_distance=0.0, lrf_lat=0.0, lrf_lon=0.0, lrf_abs_alt=0.0, focal_mm=9.1, utc="")
    base.update(kw)
    return rjpeg.Shot(**base)


def offset_m(p: geo.GroundPoint) -> tuple[float, float]:
    north = (p.lat - BASE_LAT) * geo.M_PER_DEG_LAT
    east = (p.lon - BASE_LON) * geo.M_PER_DEG_LAT * math.cos(math.radians(BASE_LAT))
    return north, east


class GeoTest(unittest.TestCase):
    def test_nadir_centre_is_below_the_drone(self):
        p = geo.pixel_to_ground(shot(), 320, 256)
        self.assertAlmostEqual(p.lat, BASE_LAT, places=7)
        self.assertAlmostEqual(p.lon, BASE_LON, places=7)
        self.assertAlmostEqual(p.distance_m, 30.0, places=3)
        self.assertAlmostEqual(p.gsd_m, 30.0 / (9.1 / 0.012), places=4)       # ~4 cm per raw pixel
        self.assertEqual(p.height_source, "takeoff")

    def test_nadir_image_orientation(self):
        # yaw 0: top of the image = north, right = east
        n, e = offset_m(geo.pixel_to_ground(shot(), 320, 0))
        self.assertGreater(n, 5)
        self.assertAlmostEqual(e, 0, places=3)
        n, e = offset_m(geo.pixel_to_ground(shot(), 640, 256))
        self.assertGreater(e, 5)
        self.assertAlmostEqual(n, 0, places=3)
        # half image width on the ground = h * (320 px / focal px)
        self.assertAlmostEqual(e, 30.0 * 320 / (9.1 / 0.012), places=2)

    def test_heading_east(self):
        n, e = offset_m(geo.pixel_to_ground(shot(gimbal_yaw=90), 320, 0))   # image top = east now
        self.assertGreater(e, 5)
        self.assertAlmostEqual(n, 0, places=3)

    def test_oblique_45_degrees(self):
        n, e = offset_m(geo.pixel_to_ground(shot(gimbal_pitch=-45, gimbal_yaw=180), 320, 256))
        self.assertAlmostEqual(n, -30.0, places=2)                                # 30 m south
        self.assertAlmostEqual(e, 0.0, places=2)

    def test_horizon_is_not_projected(self):
        self.assertIsNone(geo.pixel_to_ground(shot(gimbal_pitch=0), 320, 256))
        self.assertIsNone(geo.pixel_to_ground(shot(gps_valid=False), 320, 256))

    def test_roof_height_from_laser_rangefinder(self):
        # roof 8 m above take-off level: LRF target abs alt 308, drone 330 -> 22 m above the roof plane
        s = shot(lrf_distance=22.0, lrf_lat=BASE_LAT, lrf_lon=BASE_LON, lrf_abs_alt=308.0)
        p = geo.pixel_to_ground(s, 640, 256)
        self.assertEqual(p.height_source, "lrf")
        n, e = offset_m(p)
        self.assertAlmostEqual(e, 22.0 * 320 / (9.1 / 0.012), places=2)

    def test_m4t_focal_length(self):
        p = geo.pixel_to_ground(shot(model="M4T", focal_mm=12.0), 640, 256)
        self.assertAlmostEqual(offset_m(p)[1], 30.0 * 320 / 1000.0, places=2)


def thermal_scene(seed: int = 1) -> np.ndarray:
    """Panel rows at ~30 deg C with noise, a gradient across the image and cooler gaps between rows."""
    rng = np.random.default_rng(seed)
    t = 30.0 + rng.normal(0, 0.3, (512, 640)) + np.linspace(0, 4, 640)[None, :]
    for y in range(0, 512, 64):
        t[y:y + 8, :] -= 6.0                           # row gaps (grass)
    return t.astype(np.float32)


class DetectTest(unittest.TestCase):
    def test_clean_scene_has_no_spots(self):
        self.assertEqual(detect.find_spots(thermal_scene()), [])

    def test_spots_found_with_delta_and_class(self):
        t = thermal_scene()
        t[100:104, 200:204] += 25.0                    # cell hot spot -> class 3
        t[330:360, 400:408] += 6.0                     # warm stripe (substring) -> class 1
        t[420:424, 100:104] += 12.0                    # class 2
        spots = detect.find_spots(t)
        self.assertEqual(len(spots), 3)
        hot, mid, warm = spots                         # sorted by delta
        self.assertEqual(hot.klass, 3)
        self.assertAlmostEqual(hot.x, 201.5, places=1)
        self.assertAlmostEqual(hot.y, 101.5, places=1)
        self.assertAlmostEqual(hot.delta, 25.0 + 0.0, delta=1.5)
        self.assertEqual(mid.klass, 2)
        self.assertEqual(warm.klass, 1)
        self.assertEqual(warm.extent_px, (30.0, 8.0))

    def test_warm_row_gaps_are_not_reported(self):
        t = thermal_scene()
        for y in range(0, 512, 64):
            t[y:y + 8, :] += 11.0                      # gaps now 5 K warmer than the modules (dark soil)
        t[200:204, 300:304] += 15.0
        spots = detect.find_spots(t)
        self.assertEqual(len(spots), 1)
        self.assertAlmostEqual(spots[0].x, 301.5, places=1)

    def test_large_warm_area_is_ignored(self):
        t = thermal_scene()
        t[0:256, 0:320] += 15.0                        # a quarter of the image: warm roof, not a module fault
        self.assertEqual([s for s in detect.find_spots(t) if s.pixels > 1000], [])

    def test_thresholds_configurable(self):
        t = thermal_scene()
        t[100:104, 200:204] += 4.0
        self.assertEqual(len(detect.find_spots(t)), 1)
        self.assertEqual(detect.find_spots(t, detect.Params(min_delta=5.0)), [])
        self.assertEqual(detect.Params.from_dict({"min_delta": "5", "unknown": 1}).min_delta, 5.0)

    def test_guess_type(self):
        self.assertEqual(detect.guess_type(0.03, 0.2, 0.15), "Hotspot (Zelle/Punkt)")
        self.assertEqual(detect.guess_type(0.4, 1.1, 0.35), "Streifen (Substring/Bypass-Diode)")
        self.assertEqual(detect.guess_type(1.8, 1.7, 1.1), "Modul")
        self.assertEqual(detect.guess_type(8.0, 6.0, 1.7), "Mehrere Module/String")


def fake_rjpeg(xmp: dict, focal=(91, 10), raw_value=1000) -> bytes:
    """Minimal JPEG header with EXIF focal length, DJI XMP and APP3 raw data (no image data needed)."""
    def seg(marker, payload):
        return bytes([0xFF, marker]) + struct.pack(">H", len(payload) + 2) + payload
    # EXIF: IFD0 with one entry -> Exif IFD with FocalLength (RATIONAL)
    tiff = b"II*\x00" + struct.pack("<I", 8)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x8769, 4, 1, 26) + struct.pack("<I", 0)
    tiff += struct.pack("<H", 1) + struct.pack("<HHII", 0x920A, 5, 1, 44) + struct.pack("<I", 0)
    tiff += struct.pack("<II", *focal)
    attrs = " ".join(f'drone-dji:{k}="{v}"' for k, v in xmp.items())
    xmp_bytes = b"http://ns.adobe.com/xap/1.0/\x00<x:xmpmeta><rdf:Description " + attrs.encode() + \
        b"/></x:xmpmeta>"
    raw = np.full((512, 640), raw_value, dtype="<u2").tobytes()
    app3 = b"".join(seg(0xE3, raw[i:i + 65000]) for i in range(0, len(raw), 65000))
    return b"\xff\xd8" + seg(0xE1, b"Exif\x00\x00" + tiff) + seg(0xE1, xmp_bytes) + app3 + b"\xff\xda"


XMP = {"DroneModel": "M3T", "ImageSource": "InfraredCamera", "GpsStatus": "RTK",
       "GpsLatitude": "+51.160000000", "GpsLongitude": "+10.450000000", "AbsoluteAltitude": "+330.0",
       "RelativeAltitude": "+30.000", "GimbalYawDegree": "-12.5", "GimbalPitchDegree": "-90.0",
       "GimbalRollDegree": "+0.00", "LRFTargetDistance": "30.100", "LRFTargetLat": "51.1600010",
       "LRFTargetLon": "10.4500020", "LRFTargetAbsAlt": "299.900", "UTCAtExposure": "2026-10-02T12:00:00.0"}


class RJpegTest(unittest.TestCase):
    def test_metadata(self):
        s = rjpeg.read_shot(fake_rjpeg(XMP))
        self.assertEqual((s.model, s.gps_valid, s.has_lrf), ("M3T", True, True))
        self.assertAlmostEqual(s.focal_mm, 9.1)
        self.assertAlmostEqual(s.focal_px, 9.1 / 0.012)
        self.assertEqual((s.lat, s.lon, s.gimbal_yaw, s.gimbal_pitch), (51.16, 10.45, -12.5, -90.0))
        self.assertAlmostEqual(geo.height_above_plane(s)[0], 30.1, places=3)

    def test_raw_data(self):
        data = fake_rjpeg(XMP, raw_value=4321)
        self.assertTrue(rjpeg.is_rjpeg(data))
        raw = rjpeg.raw_thermal(data)
        self.assertEqual(raw.shape, (512, 640))
        self.assertTrue((raw == 4321).all())

    def test_invalid_gps_and_missing_focal(self):
        s = rjpeg.read_shot(fake_rjpeg(dict(XMP, GpsStatus="Invalid")))
        self.assertFalse(s.gps_valid)
        s = rjpeg.read_shot(fake_rjpeg(dict(XMP, DroneModel="M4T"), focal=(0, 0)))
        self.assertEqual(s.focal_mm, 12.0)                     # model table fallback

    def test_not_an_rjpeg(self):
        self.assertFalse(rjpeg.is_rjpeg(b"\xff\xd8\xff\xda"))
        with self.assertRaises(rjpeg.RJpegError):
            rjpeg.raw_thermal(b"\xff\xd8\xff\xda")
        with self.assertRaises(rjpeg.RJpegError):
            rjpeg.read_shot(b"not a jpeg")


@unittest.skipUnless(os.environ.get("AERONEXUS_REAL_RJPEG_DIR"), "AERONEXUS_REAL_RJPEG_DIR not set")
class RealFilesTest(unittest.TestCase):
    def test_real_thermal_images(self):
        files = sorted(Path(os.environ["AERONEXUS_REAL_RJPEG_DIR"]).glob("*_T.JPG"))
        self.assertTrue(files)
        models = set()
        for path in files:
            with self.subTest(path.name):
                data = path.read_bytes()
                self.assertTrue(rjpeg.is_rjpeg(data))
                s = rjpeg.read_shot(data)
                self.assertEqual(s.image_source, "InfraredCamera")
                self.assertIn(s.focal_mm, (9.1, 12.0))
                self.assertEqual(rjpeg.raw_thermal(data).shape, (512, 640))
                models.add(s.model)
        print("models:", sorted(models))


if __name__ == "__main__":
    unittest.main()
