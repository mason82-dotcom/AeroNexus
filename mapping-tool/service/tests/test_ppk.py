"""PPK (app/ppk.py): MRK/POS parsing, interpolation, lever arm, geo.txt, base file merge (generic geodata)."""
import unittest

from pydantic import ValidationError

from app import ppk
from app.models import JobCreate

WEEK = 2379
# DJI MRK layout (Matrice 4T, 2026-10-16): index, time of week, [week], offsets N/E/V in mm, position, std, flag
MRK = (f"1\t484577.670969\t[{WEEK}]\t   -86,N\t   132,E\t    90,V\t51.16000000,Lat\t10.45000000,Lon\t184.247,Ellh"
       f"\t0.000000, 0.000000, 0.000000\t16,Q\n"
       f"2\t484578.670969\t[{WEEK}]\t   -80,N\t   130,E\t    90,V\t51.16001000,Lat\t10.45001000,Lon\t184.300,Ellh"
       f"\t0.000000, 0.000000, 0.000000\t16,Q\n"
       "1377\t-259200.000000\t[-522]\t     0,N\t     0,E\t     0,V\t0.00000000,Lat\t0.00000000,Lon\t0.000,Ellh\n")
POS = ("% program   : RTKLIB ver.demo5 b34k\n"
       "%  GPST          latitude(deg) longitude(deg)  height(m)   Q  ns   sdn(m)   sde(m)   sdu(m)\n"
       f"{WEEK} 484577.600   51.160000000   10.450000000   184.0000   1  20   0.0100   0.0120   0.0200  0 0 0 1.0 5.0\n"
       f"{WEEK} 484577.800   51.160002000   10.450004000   184.2000   2  20   0.0300   0.0300   0.0500  0 0 0 1.0 1.2\n"
       f"{WEEK} 484580.000   51.160020000   10.450040000   185.0000   1  20   0.0100   0.0100   0.0200  0 0 0 1.0 5.0\n")


class ParseTest(unittest.TestCase):
    def test_mrk_skips_invalid_lines_and_converts_offsets(self):
        marks = ppk.parse_mrk(MRK)
        self.assertEqual([m.index for m in marks], [1, 2])
        self.assertAlmostEqual(marks[0].t, WEEK * 604800 + 484577.670969)
        self.assertEqual((marks[0].dn, marks[0].de, marks[0].dd), (-0.086, 0.132, 0.09))

    def test_pos(self):
        track = ppk.parse_pos(POS)
        self.assertEqual([f.q for f in track], [1, 2, 1])
        self.assertEqual((track[0].sdn, track[0].sde, track[0].sdu), (0.01, 0.012, 0.02))

    def test_image_index(self):
        self.assertEqual(ppk.image_index("DJI_20250815163600_0158_V.JPG"), 158)
        self.assertIsNone(ppk.image_index("other.jpg"))


class GeometryTest(unittest.TestCase):
    def setUp(self):
        self.track = ppk.parse_pos(POS)

    def test_interpolation_takes_the_worse_quality(self):
        t = WEEK * 604800 + 484577.7
        fix = ppk.interpolate(self.track, t)
        self.assertAlmostEqual(fix.lat, 51.160001, places=9)
        self.assertAlmostEqual(fix.h, 184.1, places=6)
        self.assertEqual((fix.q, fix.sdn), (2, 0.03))

    def test_no_interpolation_across_gaps_or_outside(self):
        self.assertIsNone(ppk.interpolate(self.track, WEEK * 604800 + 484579.0))      # 2.2 s gap
        self.assertIsNone(ppk.interpolate(self.track, WEEK * 604800 + 484570.0))

    def test_lever_arm_north_east_down(self):
        antenna = ppk.Fix(0, 51.16, 10.45, 200.0, 1, 0, 0, 0)
        lat, lon, h = ppk.camera_position(antenna, ppk.Mark(1, 0, dn=1.0, de=1.0, dd=0.09))
        self.assertAlmostEqual((lat - 51.16) * 111_250, 1.0, delta=0.01)                # ~1 m north
        self.assertAlmostEqual((lon - 10.45) * 111_320 * 0.6268, 1.0, delta=0.02)       # ~1 m east
        self.assertAlmostEqual(h, 199.91)                                               # camera below antenna

    def test_accuracy_floor_per_quality(self):
        self.assertEqual(ppk.accuracy(ppk.Fix(0, 0, 0, 0, 2, 0.01, 0.01, 0.02)), (0.10, 0.20))
        hacc, vacc = ppk.accuracy(ppk.Fix(0, 0, 0, 0, 1, 0.03, 0.04, 0.06))
        self.assertAlmostEqual(hacc, 0.05)
        self.assertEqual(vacc, 0.06)


class GeoTest(unittest.TestCase):
    def test_geo_txt_and_report(self):
        folder = "ppk/sdimport-TEST/DJI_202510011200_001"
        images = {folder: ["DJI_20251001120000_0001_V.JPG", "DJI_20251001120001_0002_V.JPG",
                           "DJI_20251001120003_0003_V.JPG"]}
        geo, report = ppk.build_geo(images, {folder: ppk.parse_mrk(MRK)}, ppk.parse_pos(POS))
        lines = geo.strip().splitlines()
        self.assertEqual(lines[0], "EPSG:4326")
        self.assertEqual(len(lines), 2)                   # image 2: 1.0 s after a 2.2 s gap, image 3: no mark
        f = lines[1].split()
        self.assertEqual((f[0], f[4:7]), ("DJI_20251001120000_0001_V.JPG", ["0", "0", "0"]))
        self.assertEqual(report["positioned"], 1)
        self.assertEqual(report["missing_count"], 2)
        self.assertEqual(report["quality"], {"float": 1})
        JobCreate(name="x", image_keys=["wayline/a/DJI_20251001120000_0001_V.JPG"], geo=geo)   # accepted


class BaseMergeTest(unittest.TestCase):
    HEADER = "     3.05           OBSERVATION DATA    M                   RINEX VERSION / TYPE\n" \
             "                                                            END OF HEADER\n"

    def obs(self, *seconds):
        return self.HEADER + "".join(f"> 2025 08 15 14 30 {s:10.7f}  0  1\nG05  1.0\n" for s in seconds)

    def test_merge_keeps_each_epoch_once_in_time_order(self):
        merged = ppk.merge_rinex_obs([self.obs(2, 3), self.obs(0, 1, 2)])
        epochs = [line for line in merged.splitlines() if line.startswith(">")]
        self.assertEqual([float(e.split()[6]) for e in epochs], [0, 1, 2, 3])
        self.assertEqual(merged.count("G05"), 4)
        self.assertEqual(merged.count("END OF HEADER"), 1)

    def test_obs_window(self):
        first, last = ppk.obs_window(self.obs(0, 59))
        self.assertEqual(last - first, 59)


class BaseSelectionTest(unittest.TestCase):
    def test_only_files_covering_the_flight_are_unpacked(self):
        span = ppk.name_span("KARL00DEU_S_20252271430_15M_01S_MO.crx")
        self.assertEqual(span[1] - span[0], 900)
        flight = (span[0] + 320, span[0] + 590)                     # 14:35:20 - 14:39:50
        self.assertTrue(ppk.wanted("KARL00DEU_S_20252271430_15M_01S_MO.crx", [flight]))
        self.assertFalse(ppk.wanted("KARL00DEU_S_20252271415_15M_01S_MO.crx", [flight]))  # ends 5:20 before
        self.assertTrue(ppk.wanted("KARL00DEU_S_20252271415_15M_01S_MO.crx", [(span[0] + 60, span[0] + 120)]))
        self.assertFalse(ppk.wanted("KARL00DEU_S_20252271200_15M_01S_MO.crx", [flight]))
        self.assertTrue(ppk.wanted("KARL00DEU_R_20252270000_01D_MN.rnx", [flight]))        # day nav file
        self.assertTrue(ppk.wanted("vrs_kapelle.25o", [flight]))                             # no long name


class JobGeoValidationTest(unittest.TestCase):
    def test_bad_geo_is_refused(self):
        for geo in ("WGS84\nA.JPG 1 2 3\n", "EPSG:4326\n", "EPSG:4326\nA.JPG x 2 3\n", "EPSG:4326\n../x 1 2 3\n"):
            with self.assertRaises(ValidationError):
                JobCreate(name="x", image_keys=["k/A.JPG"], geo=geo)


if __name__ == "__main__":
    unittest.main()
