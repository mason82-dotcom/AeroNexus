"""Media ingest: classification, shot keys and flight-set assignment (no database, no MinIO)."""
import unittest
from collections import Counter
from datetime import datetime, timedelta

from app import ingest, mediameta


class ClassifyTest(unittest.TestCase):
    def test_dji_suffixes(self):
        cases = {
            "DJI_20261001224903_0001_T.JPG": ("thermal", None),
            "DJI_20261001224904_0001_V.JPG": ("visible", None),
            "DJI_20261002180010_0003_W.JPG": ("rgb", None),
            "DJI_20261002180010_0003_Z.JPG": ("rgb", None),
            "DJI_20261015140306_0003_D.JPG": ("rgb", None),
            "DJI_20261015140306_0003_F.JPG": ("multispectral", "F"),
            "DJI_20261015140306_0003_MS_NIR.TIF": ("multispectral", "NIR"),
            "DJI_20261015140306_0003_MS_RE.TIF": ("multispectral", "RE"),
            "DJI_20261015140306_0003_MS_R.TIF": ("multispectral", "R"),
            "DJI_20261015140306_0003_MS_G.TIF": ("multispectral", "G"),
            "DJI_20261001223827_0002_D.MP4": ("video", None),
            "DJI_20261001224920_0004_S.MP4": ("video", None),
            "notes.txt": ("other", None),
        }
        for name, expected in cases.items():
            with self.subTest(name=name):
                self.assertEqual(ingest.classify(name), expected)

    def test_panorama_flag_from_pilot(self):
        self.assertEqual(ingest.classify("DJI_20261002180010_0007_W.JPG", 1), ("panorama", None))


class ShotTest(unittest.TestCase):
    def test_all_files_of_a_shot_share_the_key(self):
        keys = {ingest.shot_info(f"DJI_20261015140306_0003_{s}")[0]
                for s in ("D.JPG", "F.JPG", "MS_NIR.TIF", "MS_R.TIF")}
        self.assertEqual(keys, {"DJI_20261015140306_0003"})
        self.assertEqual(ingest.shot_info("DJI_20261015140306_0003_D.JPG")[1], datetime(2026, 10, 15, 14, 3, 6))

    def test_files_of_one_shot_may_differ_by_a_second(self):
        f_key, f_time = ingest.shot_info("DJI_20261001223817_0001_F.JPG")
        d_key, d_time = ingest.shot_info("DJI_20261001223818_0001_D.JPG")
        self.assertTrue(ingest.same_shot(f_key, f_time, d_key, d_time))
        other_key, other_time = ingest.shot_info("DJI_20261001223818_0002_D.JPG")
        self.assertFalse(ingest.same_shot(f_key, f_time, other_key, other_time))   # next image number
        late_key, late_time = ingest.shot_info("DJI_20261001223820_0001_D.JPG")
        self.assertFalse(ingest.same_shot(f_key, f_time, late_key, late_time))     # 3 s apart

    def test_foreign_names(self):
        self.assertEqual(ingest.shot_info("IMG_0001.JPG"), (None, None))
        self.assertEqual(ingest.shot_info("DJI_20261399999999_0001_W.JPG")[1], None)  # invalid date


class FlightSetTest(unittest.TestCase):
    T0 = datetime(2026, 10, 15, 14, 2, 40)

    def test_out_of_order_uploads_join_one_set(self):
        sets = [{"id": "a", "first_capture": self.T0 + timedelta(seconds=26),
                 "last_capture": self.T0 + timedelta(seconds=26)}]       # shot 3 arrived first
        self.assertEqual(ingest.find_set(sets, self.T0)["id"], "a")      # shot 1, 26 s earlier

    def test_gap_starts_a_new_set(self):
        sets = [{"id": "a", "first_capture": self.T0, "last_capture": self.T0 + timedelta(minutes=10)}]
        self.assertEqual(ingest.find_set(sets, self.T0 + timedelta(minutes=24))["id"], "a")
        self.assertIsNone(ingest.find_set(sets, self.T0 + timedelta(minutes=26)))
        self.assertIsNone(ingest.find_set(sets, self.T0 - timedelta(minutes=16)))

    def test_closest_set_wins(self):
        sets = [{"id": "early", "first_capture": self.T0, "last_capture": self.T0},
                {"id": "late", "first_capture": self.T0 + timedelta(minutes=20),
                 "last_capture": self.T0 + timedelta(minutes=20)}]
        self.assertEqual(ingest.find_set(sets, self.T0 + timedelta(minutes=12))["id"], "late")

    def test_kind(self):
        self.assertEqual(ingest.set_kind(Counter(rgb=3, multispectral=9)), "multispectral")
        self.assertEqual(ingest.set_kind(Counter(thermal=5, visible=5)), "thermal")
        self.assertEqual(ingest.set_kind(Counter(rgb=4, panorama=1)), "rgb")
        self.assertEqual(ingest.set_kind(Counter(visible=7)), "rgb")
        self.assertEqual(ingest.set_kind(Counter(video=2)), "video")
        self.assertEqual(ingest.set_kind(Counter(rgb=1, video=1)), "mixed")



class DroneAttributionTest(unittest.TestCase):
    """Pilot 2 attributes uploads to the drone connected at upload time; the aircraft writes its own serial."""
    XMP = (b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
           b'drone-dji:DroneSerialNumber="%s" drone-dji:CameraSerialNumber="1TCTL2J03BJ0D0" '
           b'drone-dji:RelativeAltitude="+53.0"/></rdf:RDF></x:xmpmeta>')

    def test_serials_from_xmp(self):
        meta = mediameta.extract(self.XMP % b"1581F5FJD233600D2460", 1000, "DJI_20241019124500_0708_T.JPG")
        self.assertEqual((meta["drone_serial"], meta["camera_serial"]), ("1581F5FJD233600D2460", "1TCTL2J03BJ0D0"))
        self.assertIsNone(mediameta.extract(self.XMP % b"../x", 1000, "a.JPG")["drone_serial"])

    def test_aircraft_serial_wins_over_pilot(self):
        # 2026-10-15: M3T photos from an SD card in the Mavic 3E were reported with the M3E serial
        self.assertEqual(ingest.effective_drone({"drone_serial": "1581F5FJD233600D2460"}, "1581F5FHD241D00D28BZ"),
                         "1581F5FJD233600D2460")
        self.assertEqual(ingest.effective_drone({"drone_serial": None}, "1581F5FHD241D00D28BZ"), "1581F5FHD241D00D28BZ")
        self.assertEqual(ingest.effective_drone(None, None), "")


if __name__ == "__main__":
    unittest.main()
