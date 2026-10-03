"""Executable path (waylines.wpml) generated for copies and planned routes (app/wpml.build_waylines).

Pilot 2 flies the waylines.wpml of a synced route as it is (it does not recompute area routes), so the
generated path must match the planned lanes, height, speed and photo interval exactly.
"""
import math
import os
import re
import unittest
from pathlib import Path

from app import planner, wpml
from tests import kmz_fixture as fx

PARAMS = {"height": 75, "direction": 30, "margin": 0, "overlap_h": 80, "overlap_w": 70, "speed": 8,
          "image_format": None}


def plan_params(poly, params, lens=("1-66-0", "wide")):
    ln = planner.lens(*lens)
    across, along = planner.footprint_m(ln, params["height"])
    lanes = planner.flight_lines(poly, params["direction"], across * (1 - params["overlap_w"] / 100), params["margin"])
    return dict(params, path={"lanes": lanes, "photo_spacing": along * (1 - params["overlap_h"] / 100)}), lanes


def rect(w_m=300, h_m=200):
    dlon = w_m / (111320 * math.cos(math.radians(fx.BASE_LAT)))
    dlat = h_m / 111320
    b = [fx.BASE_LON, fx.BASE_LAT]
    return [[b[0], b[1]], [b[0] + dlon, b[1]], [b[0] + dlon, b[1] + dlat], [b[0], b[1] + dlat]]


def azimuth(a, b):
    k = math.cos(math.radians(a[1]))
    return math.degrees(math.atan2((b[0] - a[0]) * k, b[1] - a[1])) % 180


class GeneratedPathTest(unittest.TestCase):
    def setUp(self):
        self.poly = rect()
        self.params, self.lanes = plan_params(self.poly, PARAMS)
        self.out = wpml.edit_copy(fx.make_kmz(), self.poly, self.params)
        self.wl = fx.read(self.out, "wpmz/waylines.wpml").decode()
        self.tpl = fx.read(self.out, "wpmz/template.kml").decode()

    def test_replaces_the_template_path(self):
        self.assertNotEqual(self.wl, fx.waylines_text())
        route = wpml.parse(self.out)
        pts = [p for lane in self.lanes for p in lane]
        self.assertEqual(len(route.flight_path), len(pts))                      # 2 points per lane
        for got, want in zip(route.flight_path, pts):
            self.assertAlmostEqual(got[0], want[0], places=9)                   # lon first
            self.assertAlmostEqual(got[1], want[1], places=9)
            self.assertEqual(got[2], 75)                                       # executeHeight

    def test_lanes_run_in_the_planned_direction(self):
        route = wpml.parse(self.out)
        self.assertAlmostEqual(azimuth(route.flight_path[0], route.flight_path[1]), 30, delta=0.2)
        self.assertAlmostEqual(azimuth(route.flight_path[2], route.flight_path[3]), 30, delta=0.2)

    def test_height_mode_actions_and_lens(self):
        self.assertIn("<wpml:executeHeightMode>relativeToStartPoint</wpml:executeHeightMode>", self.wl)
        self.assertEqual(self.wl.count("<wpml:actionActuatorFunc>startTimeLapse<"), 1)
        self.assertEqual(self.wl.count("<wpml:actionActuatorFunc>stopTimeLapse<"), 1)
        self.assertIn("<wpml:gimbalPitchRotateAngle>-90</wpml:gimbalPitchRotateAngle>", self.wl)
        n = len(self.lanes) * 2
        self.assertIn(f"<wpml:actionGroupEndIndex>{n - 1}</wpml:actionGroupEndIndex>", self.wl)
        self.assertEqual(re.findall(r"<wpml:payloadLensIndex>([^<]*)<", self.wl), ["visable", "visable"])
        # photo every along-footprint * 20 % at 8 m/s
        along = planner.footprint_m(planner.lens("1-66-0", "wide"), 75)[1]
        interval = float(re.findall(r"<wpml:minShootInterval>([^<]*)<", self.wl)[0])
        self.assertAlmostEqual(interval, along * 0.2 / 8, places=2)
        self.assertEqual(self.params["path_info"]["lanes"], len(self.lanes))

    def test_template_switches_that_change_the_path_are_off(self):
        self.assertIn("<wpml:surfaceFollowModeEnable>0</wpml:surfaceFollowModeEnable>", self.tpl)
        self.assertIn("<wpml:isRealtimeSurfaceFollow>0</wpml:isRealtimeSurfaceFollow>", self.tpl)
        self.assertIn("<wpml:direction>30</wpml:direction>", self.tpl)

    def test_xml_is_well_formed_and_indices_consecutive(self):
        from defusedxml.ElementTree import fromstring
        root = fromstring(self.wl.encode())
        ns = {"k": "http://www.opengis.net/kml/2.2", "w": "http://www.dji.com/wpmz/1.0.6"}
        idx = [int(e.text) for e in root.findall(".//k:Placemark/w:index", ns)]
        self.assertEqual(idx, list(range(len(idx))))

    def test_terrain_follow_like_pilot(self):
        params, _ = plan_params(self.poly, dict(PARAMS, terrain_follow=True, height=60))
        out = wpml.edit_copy(fx.make_kmz(), self.poly, params)
        wl = fx.read(out, "wpmz/waylines.wpml").decode()
        tpl = fx.read(out, "wpmz/template.kml").decode()
        self.assertIn("<wpml:executeHeightMode>realTimeFollowSurface</wpml:executeHeightMode>", wl)
        self.assertIn("<wpml:surfaceFollowModeEnable>1</wpml:surfaceFollowModeEnable>", tpl)
        self.assertIn("<wpml:isRealtimeSurfaceFollow>1</wpml:isRealtimeSurfaceFollow>", tpl)
        self.assertIn("<wpml:surfaceRelativeHeight>60</wpml:surfaceRelativeHeight>", tpl)
        self.assertIn("<wpml:executeHeight>60</wpml:executeHeight>", wl)
        self.assertTrue(params["path_info"]["terrain_follow"])

    def test_without_terrain_follow_switches_are_off(self):
        self.assertIn("<wpml:executeHeightMode>relativeToStartPoint</wpml:executeHeightMode>", self.wl)
        self.assertIn("<wpml:surfaceFollowModeEnable>0</wpml:surfaceFollowModeEnable>", self.tpl)
        self.assertFalse(self.params["path_info"]["terrain_follow"])

    def test_speed_is_reduced_to_keep_the_overlap(self):
        params, _ = plan_params(self.poly, dict(PARAMS, speed=15, height=30))
        out = wpml.edit_copy(fx.make_kmz(), self.poly, params)
        self.assertGreaterEqual(params["path_info"]["shot_interval_s"], wpml.MIN_SHOT_INTERVAL - 0.01)
        self.assertLess(params["path_info"]["speed"], 15)
        # template shows the same (reduced) speed as the executable path
        tpl_speed = float(re.findall(r"<wpml:autoFlightSpeed>([^<]*)<", fx.read(out, "wpmz/template.kml").decode())[0])
        wl_speed = float(re.findall(r"<wpml:autoFlightSpeed>([^<]*)<", fx.read(out, "wpmz/waylines.wpml").decode())[0])
        self.assertAlmostEqual(tpl_speed, wl_speed, places=2)
        self.assertAlmostEqual(tpl_speed, params["path_info"]["speed"], places=2)

    def test_absolute_height_mode_is_refused(self):
        kmz = fx.make_kmz()
        import io
        import zipfile
        text = fx.template_text().replace("relativeToStartPoint", "EGM96")
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("wpmz/template.kml", text)
            zf.writestr("wpmz/waylines.wpml", fx.read(kmz, "wpmz/waylines.wpml"))
        with self.assertRaisesRegex(wpml.WpmlError, "EGM96"):
            wpml.edit_copy(buf.getvalue(), self.poly, dict(self.params))

    def test_thermal_lens_and_image_format(self):
        params, lanes = plan_params(self.poly, dict(PARAMS, image_format="ir"), ("1-67-0", "thermal"))
        text = fx.template_text().replace("      <Placemark>\n", "      <wpml:payloadParam>\n"
                                          "        <wpml:imageFormat>visable</wpml:imageFormat>\n"
                                          "      </wpml:payloadParam>\n      <Placemark>\n", 1)
        import io
        import zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("wpmz/template.kml", text)
            zf.writestr("wpmz/waylines.wpml", fx.waylines_text())
        out = wpml.edit_copy(buf.getvalue(), self.poly, params)
        wl = fx.read(out, "wpmz/waylines.wpml").decode()
        self.assertEqual(re.findall(r"<wpml:payloadLensIndex>([^<]*)<", wl), ["ir", "ir"])
        self.assertGreater(len(lanes), len(self.lanes))                   # thermal footprint is much smaller

    def test_empty_area(self):
        params = dict(PARAMS, path={"lanes": [], "photo_spacing": 10})
        with self.assertRaises(wpml.WpmlError):
            wpml.edit_copy(fx.make_kmz(), self.poly, params)


@unittest.skipUnless(os.environ.get("AERONEXUS_REAL_KMZ_DIR"), "AERONEXUS_REAL_KMZ_DIR not set")
class RealTemplatesTest(unittest.TestCase):
    def test_real_templates_keep_their_start_actions(self):
        tested = 0
        for path in sorted(Path(os.environ["AERONEXUS_REAL_KMZ_DIR"]).glob("*.kmz")):
            kmz = path.read_bytes()
            route = wpml.parse(kmz)
            if route.template_type != "mapping2d":
                continue
            with self.subTest(path.name):
                lens = ("1-67-0", "thermal") if "ir" in (route.image_format or "") else ("1-66-0", "wide")
                params, lanes = plan_params(route.polygon, dict(PARAMS, height=60), lens)
                out = wpml.edit_copy(kmz, route.polygon, params)
                old = fx.read(kmz, "wpmz/waylines.wpml").decode()
                new = fx.read(out, "wpmz/waylines.wpml").decode()
                m = re.search(r"<wpml:startActionGroup>.*?</wpml:startActionGroup>", old, re.S)
                if m:
                    self.assertIn(m.group(0), new)                              # camera specific start actions
                self.assertEqual(new[:new.index("<Folder>")], old[:old.index("<Folder>")])   # mission config
                self.assertEqual(len(wpml.parse(out).flight_path), 2 * len(lanes))
                self.assertNotIn("SmartOblique", new)
                tested += 1
        self.assertGreater(tested, 0)


if __name__ == "__main__":
    unittest.main()
