"""Flight planning (app/planner.py) and the image format edit (app/wpml.py)."""
import math
import re
import unittest

from app import planner, wpml
from tests import kmz_fixture as fx

LAT, LON = 51.16, 10.45


def rect(width_m: float, height_m: float) -> list[list[float]]:
    """width east-west, height north-south, south-west corner at the test site."""
    dlon = width_m / (planner.M_PER_DEG * math.cos(math.radians(LAT)))
    dlat = height_m / planner.M_PER_DEG
    return [[LON, LAT], [LON + dlon, LAT], [LON + dlon, LAT + dlat], [LON, LAT + dlat]]


def seg_azimuth(seg) -> float:
    (x1, y1), (x2, y2) = seg
    return math.degrees(math.atan2((x2 - x1) * math.cos(math.radians(LAT)), y2 - y1)) % 180


class CameraTest(unittest.TestCase):
    def test_gsd_matches_dji_specs(self):
        # DJI: M3E H/37.5 cm, M3M multispectral H/18.9 cm, M3T thermal H/7.6 cm
        self.assertAlmostEqual(planner.gsd_cm(planner.lens("1-66-0", "wide"), 100), 100 / 37.5, places=2)
        self.assertAlmostEqual(planner.gsd_cm(planner.lens("1-68-0", "ms"), 100), 100 / 18.9, places=2)
        self.assertAlmostEqual(planner.gsd_cm(planner.lens("1-67-0", "thermal"), 100), 100 / 7.6, delta=0.05)

    def test_height_for_pv_thermography(self):
        # 3 cm/px thermal (>= 5 px per 15.6 cm cell): M3T ~22.7 m, M4T 30 m
        self.assertAlmostEqual(planner.height_for_gsd(planner.lens("1-67-0", "thermal"), 3.0), 22.75, places=1)
        self.assertAlmostEqual(planner.height_for_gsd(planner.lens("1-89-0", "thermal"), 3.0), 30.0, places=1)

    def test_payload_keys_and_presets(self):
        self.assertEqual(planner.payload_type("1-67-0"), 67)
        self.assertEqual(planner.payload_type("67-2"), 67)
        self.assertEqual(set(planner.presets_for("1-66-0")), {"ortho"})
        self.assertEqual(set(planner.presets_for("1-67-0")), {"ortho", "pv_thermal"})
        self.assertEqual(set(planner.presets_for("1-68-0")), {"ortho", "multispectral"})
        with self.assertRaises(planner.PlanError):
            planner.camera("1-99-0")
        with self.assertRaises(planner.PlanError):
            planner.lens("1-66-0", "thermal")

    def test_speed_from_photo_interval(self):
        ln = planner.lens("1-67-0", "thermal")
        # along footprint at 22.75 m: 512 * 3 cm = 15.4 m; 25 % new per photo = 3.84 m every 2 s
        self.assertAlmostEqual(planner.speed_for(ln, 22.75, 75, 5.0), 1.9, places=1)
        self.assertEqual(planner.speed_for(planner.lens("1-66-0", "wide"), 100, 50, 12.0), 12.0)   # capped


class GeometryTest(unittest.TestCase):
    def test_area_and_longest_edge(self):
        self.assertAlmostEqual(planner.area_m2(rect(200, 100)), 20000, delta=20)
        self.assertEqual(planner.longest_edge_direction(rect(200, 100)), 90)        # east-west edge
        self.assertEqual(planner.longest_edge_direction(rect(100, 200)), 0)         # north-south edge

    def test_lines_follow_direction_and_cover_the_area(self):
        poly = rect(200, 100)
        lines = planner.flight_lines(poly, 90, 20)                                   # east-west lines
        self.assertEqual(len(lines), 5)                                              # 100 m / 20 m
        for seg in lines:
            self.assertAlmostEqual(seg_azimuth(seg), 90, delta=0.5)
        lats = sorted(seg[0][1] for seg in lines)
        gaps = [(b - a) * planner.M_PER_DEG for a, b in zip(lats, lats[1:])]
        self.assertTrue(all(abs(g - 20) < 0.1 for g in gaps))
        # alternating (lawnmower): consecutive lines start at opposite ends
        self.assertGreater(abs(lines[0][0][0] - lines[1][0][0]), 0.001)

    def test_lines_north_south(self):
        lines = planner.flight_lines(rect(200, 100), 0, 25)
        self.assertEqual(len(lines), 8)
        self.assertAlmostEqual(seg_azimuth(lines[0]), 0, delta=0.5)

    def test_margin_extends_lines(self):
        a = planner.flight_lines(rect(200, 100), 90, 20)
        b = planner.flight_lines(rect(200, 100), 90, 20, margin=10)
        la = planner._seg_len(a[0])
        lb = planner._seg_len(b[0])
        self.assertAlmostEqual(lb - la, 20, delta=0.5)

    def test_triangle_lines_get_shorter(self):
        tri = [[LON, LAT], [LON + 0.003, LAT], [LON, LAT + 0.002]]
        lines = planner.flight_lines(tri, 90, 20)
        lengths = [planner._seg_len(s) for s in lines]
        self.assertTrue(lengths == sorted(lengths) or lengths == sorted(lengths, reverse=True))
        self.assertGreater(max(lengths), 3 * min(lengths))


class PreviewTest(unittest.TestCase):
    def test_pv_preview(self):
        ln = planner.lens("1-67-0", "thermal")
        h = planner.height_for_gsd(ln, 3.0)
        p = planner.preview(rect(200, 100), "1-67-0", "thermal", h, 75, 50, 90, 1.9)
        self.assertAlmostEqual(p["gsd_cm"], 3.0, places=2)
        self.assertAlmostEqual(p["footprint_m"][0], 640 * 0.03, delta=0.1)
        self.assertAlmostEqual(p["line_spacing_m"], 9.6, delta=0.1)                  # 19.2 m * 50 %
        self.assertEqual(p["line_count"], 11)
        self.assertAlmostEqual(p["area_ha"], 2.0, delta=0.01)
        self.assertGreater(p["photos"], 11 * 200 / 3.84 * 0.9)
        self.assertEqual(p["warnings"], [])

    def test_warnings(self):
        p = planner.preview(rect(200, 100), "1-66-0", "wide", 150, 80, 70, 0, 15)
        self.assertEqual(len(p["warnings"]), 1)                                      # height only
        p = planner.preview(rect(200, 100), "1-67-0", "thermal", 22.75, 75, 50, 0, 6)
        self.assertTrue(any("m/s" in w for w in p["warnings"]))                       # too fast for overlap


class ImageFormatEditTest(unittest.TestCase):
    PARAMS = {"height": 70, "direction": 0, "margin": 0, "overlap_h": 80, "overlap_w": 80, "speed": 15}

    def kmz_with_format(self, fmt="visable") -> bytes:
        text = fx.template_text().replace("      <Placemark>\n", "      <wpml:payloadParam>\n"
                                          f"        <wpml:imageFormat>{fmt}</wpml:imageFormat>\n"
                                          "      </wpml:payloadParam>\n      <Placemark>\n", 1)
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("wpmz/template.kml", text)
            zf.writestr("wpmz/waylines.wpml", fx.waylines_text())
        return buf.getvalue()

    def test_parse_and_change_image_format(self):
        kmz = self.kmz_with_format()
        self.assertEqual(wpml.parse(kmz).image_format, "visable")
        out = wpml.edit_copy(kmz, fx.POLYGON, dict(self.PARAMS, image_format="visable,ir"))
        self.assertEqual(wpml.parse(out).image_format, "visable,ir")
        text = fx.read(out, "wpmz/template.kml").decode()
        self.assertEqual(len(re.findall("<wpml:imageFormat>", text)), 1)

    def test_unchanged_without_image_format(self):
        out = wpml.edit_copy(self.kmz_with_format("ir"), fx.POLYGON, dict(self.PARAMS, image_format=None))
        self.assertEqual(wpml.parse(out).image_format, "ir")

    def test_template_without_tag(self):
        with self.assertRaises(wpml.WpmlError):
            wpml.edit_copy(fx.make_kmz(), fx.POLYGON, dict(self.PARAMS, image_format="ir"))


if __name__ == "__main__":
    unittest.main()
