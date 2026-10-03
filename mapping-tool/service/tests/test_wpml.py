"""Route editing (app/wpml.py). Guards the Pilot 2 findings from CLAUDE.md:
lon/lat order (a re-serialized template put the route into Somalia), text-preserving edits and
waylines.wpml kept in the KMZ ("route file deleted" otherwise).

Optional: AERONEXUS_REAL_KMZ_DIR=<dir with Pilot 2 *.kmz> also runs the edit round trip on real files
(never commit real route files: they contain the flight site).
"""
import os
import re
import unittest
from pathlib import Path

from app import wpml
from tests import kmz_fixture as fx

PARAMS = {"height": 70, "direction": 0, "margin": 0, "overlap_h": 80, "overlap_w": 80, "speed": 15}
SHIFTED = [[lon + 0.0005, lat + 0.0003] for lon, lat in fx.POLYGON]


def strip_volatile(text: str) -> str:
    """Template text without the parts an edit is allowed to change (polygon, timestamps)."""
    text = re.sub(r"<coordinates>.*?</coordinates>", "<coordinates/>", text, flags=re.S)
    return re.sub(r"<wpml:(createTime|updateTime)>\d+<", r"<wpml:\1>T<", text)


class ParseTest(unittest.TestCase):
    def test_polygon_is_lon_lat_without_closing_point(self):
        for closed in (False, True):
            route = wpml.parse(fx.make_kmz(closed=closed))
            self.assertEqual(route.polygon, fx.POLYGON)
            lon, lat = route.polygon[0]
            self.assertAlmostEqual(lon, fx.BASE_LON)       # lon first: [10.45, 51.16], not [51.16, 10.45]
            self.assertAlmostEqual(lat, fx.BASE_LAT)

    def test_params_and_type(self):
        route = wpml.parse(fx.make_kmz())
        self.assertEqual(route.template_type, "mapping2d")
        self.assertEqual(route.wpml_ns, "http://www.dji.com/wpmz/1.0.6")
        self.assertEqual(route.params["height"], 70)
        self.assertEqual(route.params["overlap_h"], 80)
        self.assertEqual(route.params["speed"], 15)
        self.assertEqual(route.params["height_mode"], "relativeToStartPoint")
        self.assertTrue(route.params["surface_follow"])

    def test_flight_path_from_waylines(self):
        route = wpml.parse(fx.make_kmz())
        self.assertEqual([p[:2] for p in route.flight_path], fx.PATH)
        self.assertTrue(all(p[2] == 70 for p in route.flight_path))

    def test_invalid_files(self):
        with self.assertRaises(wpml.WpmlError):
            wpml.parse(b"not a zip")
        with self.assertRaises(wpml.WpmlError):
            wpml.parse(fx.make_kmz(with_template=False))


class EditCopyTest(unittest.TestCase):
    def test_polygon_replaced_in_lon_lat_order(self):
        out = wpml.edit_copy(fx.make_kmz(), SHIFTED, PARAMS)
        route = wpml.parse(out)
        self.assertEqual(len(route.polygon), len(SHIFTED))
        for got, want in zip(route.polygon, SHIFTED):
            self.assertAlmostEqual(got[0], want[0], places=9)
            self.assertAlmostEqual(got[1], want[1], places=9)

    def test_closed_ring_stays_closed_open_stays_open(self):
        for closed in (False, True):
            text = fx.read(wpml.edit_copy(fx.make_kmz(closed=closed), SHIFTED, PARAMS),
                           "wpmz/template.kml").decode()
            coords = re.search(r"<coordinates>(.*?)</coordinates>", text, re.S).group(1).split()
            self.assertEqual(len(coords), len(SHIFTED) + (1 if closed else 0))
            self.assertEqual(coords[0] == coords[-1], closed)

    def test_unchanged_params_keep_template_byte_identical(self):
        original = fx.template_text()
        edited = fx.read(wpml.edit_copy(fx.make_kmz(), SHIFTED, PARAMS), "wpmz/template.kml").decode()
        self.assertEqual(strip_volatile(edited), strip_volatile(original))
        # same indentation and number style for the coordinates as Pilot 2 wrote them
        first = re.search(r"<coordinates>\n( +)(\S+)", edited)
        self.assertEqual(first.group(1), " " * 16)
        self.assertTrue(first.group(2).endswith(",0"))

    def test_height_change_updates_all_height_fields(self):
        out = wpml.edit_copy(fx.make_kmz(), fx.POLYGON, dict(PARAMS, height=90))
        text = fx.read(out, "wpmz/template.kml").decode()
        for name in ("globalShootHeight", "height", "surfaceRelativeHeight"):
            self.assertIn(f"<wpml:{name}>90</wpml:{name}>", text)
        self.assertIn("<wpml:ellipsoidHeight>140</wpml:ellipsoidHeight>", text)   # 120 + (90 - 70)

    def test_other_params(self):
        params = dict(PARAMS, direction=45, margin=5, overlap_h=75, overlap_w=65, speed=8.5)
        text = fx.read(wpml.edit_copy(fx.make_kmz(), fx.POLYGON, params), "wpmz/template.kml").decode()
        self.assertIn("<wpml:direction>45</wpml:direction>", text)
        self.assertIn("<wpml:margin>5</wpml:margin>", text)
        self.assertIn("<wpml:orthoCameraOverlapH>75</wpml:orthoCameraOverlapH>", text)
        self.assertIn("<wpml:orthoLidarOverlapW>65</wpml:orthoLidarOverlapW>", text)
        self.assertIn("<wpml:autoFlightSpeed>8.5</wpml:autoFlightSpeed>", text)

    def test_values_ending_in_zero(self):
        # regression: _fmt stripped the zero of whole numbers (overlap 70 -> 7, direction 90 -> 9, 2026-10-03)
        params = dict(PARAMS, height=100, direction=90, margin=10, overlap_h=60, overlap_w=70, speed=10)
        text = fx.read(wpml.edit_copy(fx.make_kmz(), fx.POLYGON, params), "wpmz/template.kml").decode()
        for tag, value in (("direction", "90"), ("margin", "10"), ("orthoCameraOverlapH", "60"),
                           ("orthoCameraOverlapW", "70"), ("orthoLidarOverlapW", "70"), ("autoFlightSpeed", "10"),
                           ("globalShootHeight", "100"), ("height", "100")):
            self.assertIn(f"<wpml:{tag}>{value}</wpml:{tag}>", text, tag)
        for v in (0, 7.29, 7.5, 180, 359):
            self.assertEqual(float(wpml._fmt(v, 3)), v)
        self.assertEqual([wpml._fmt(v, 0) for v in (90, 100, 0, 45)], ["90", "100", "0", "45"])

    def test_waylines_kept_unchanged(self):
        kmz = fx.make_kmz()
        out = wpml.edit_copy(kmz, SHIFTED, PARAMS)
        self.assertEqual(fx.read(out, "wpmz/waylines.wpml"), fx.read(kmz, "wpmz/waylines.wpml"))

    def test_xml_declaration_and_prefix_kept(self):
        text = fx.read(wpml.edit_copy(fx.make_kmz(), SHIFTED, PARAMS), "wpmz/template.kml").decode()
        self.assertTrue(text.startswith('<?xml version="1.0" encoding="UTF-8"?>\n<kml xmlns='))
        self.assertNotIn("ns0:", text)                     # ElementTree re-serialization would add ns0/ns1

    def test_only_mapping2d_is_editable(self):
        for ttype in ("waypoint", "mappingPrism", "mappingCylinder"):
            with self.assertRaises(wpml.WpmlError):
                wpml.edit_copy(fx.make_kmz(template_type=ttype), SHIFTED, PARAMS)


@unittest.skipUnless(os.environ.get("AERONEXUS_REAL_KMZ_DIR"), "AERONEXUS_REAL_KMZ_DIR not set")
class RealPilotFilesTest(unittest.TestCase):
    def test_round_trip(self):
        files = sorted(Path(os.environ["AERONEXUS_REAL_KMZ_DIR"]).glob("*.kmz"))
        tested = 0
        for path in files:
            kmz = path.read_bytes()
            route = wpml.parse(kmz)
            if route.template_type != "mapping2d":
                continue
            with self.subTest(path.name):
                params = {k: route.params[k] for k in PARAMS}
                shifted = [[lon + 0.0001, lat + 0.0001] for lon, lat in route.polygon]
                out = wpml.edit_copy(kmz, shifted, params)
                again = wpml.parse(out)
                for got, want in zip(again.polygon, shifted):
                    self.assertAlmostEqual(got[0], want[0], places=9)
                    self.assertAlmostEqual(got[1], want[1], places=9)
                self.assertEqual(strip_volatile(fx.read(out, "wpmz/template.kml").decode()),
                                 strip_volatile(fx.read(kmz, "wpmz/template.kml").decode()))
                tested += 1
        self.assertGreater(tested, 0, "no mapping2d KMZ found")


if __name__ == "__main__":
    unittest.main()
