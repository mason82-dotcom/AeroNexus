"""Media list filters (app/mediabrowse.py): SQL fragment and parameters (no database)."""
import unittest
from datetime import date, datetime

from app import mediabrowse


class FilterTest(unittest.TestCase):
    def test_no_filter(self):
        self.assertEqual(mediabrowse.build_filters(None, None, None, None, None, None), ("", []))

    def test_all_filters(self):
        sql, params = mediabrowse.build_filters("thermal,visible", "1581F5FJD233600D2460", "36d0139c",
                                                date(2024, 10, 19), date(2024, 10, 22), "0708_T")
        self.assertIn("i.category IN (%s,%s)", sql)
        self.assertEqual(params[:4], ["thermal", "visible", "1581F5FJD233600D2460", "36d0139c%"])
        self.assertEqual(params[4:6], [datetime(2024, 10, 19), datetime(2024, 10, 23)])      # date_to inclusive
        self.assertEqual(params[6], "%0708\\_T%")                                           # LIKE escaped

    def test_files_without_drone(self):
        sql, params = mediabrowse.build_filters(None, "-", None, None, None, None)
        self.assertIn("= ''", sql)
        self.assertEqual(params, [])

    def test_invalid_values_are_refused(self):
        for args in (("x", None, None), (None, "abc'; --", None), (None, None, "../x")):
            with self.assertRaises(ValueError):
                mediabrowse.build_filters(*args, None, None, None)


if __name__ == "__main__":
    unittest.main()


class FlightSetSummaryTest(unittest.TestCase):
    def test_classification(self):
        self.assertEqual(mediabrowse.classify_set(53.4, 0.97, 731), "mapping")
        self.assertEqual(mediabrowse.classify_set(111.7, 0.22, 1374), "oblique")    # smart oblique
        self.assertEqual(mediabrowse.classify_set(-0.0, 0.0, 34), "ground")
        self.assertEqual(mediabrowse.classify_set(30.0, 1.0, 5), "flight")
        self.assertEqual(mediabrowse.classify_set(None, None, 3), "unknown")

    def test_summary_ignores_a_wrong_fix_for_the_extent(self):
        metas = [{"rel_alt": 53.0, "gimbal_pitch": -90.0, "gps_status": "RTK", "lat": 51.16 + i * 1e-4, "lon": 10.45}
                 for i in range(10)]
        metas.append({"rel_alt": 0.0, "gimbal_pitch": 0.0, "gps_status": "Normal", "lat": 37.0, "lon": 10.45})
        s = mediabrowse.set_summary(metas)
        self.assertEqual((s["rel_alt_median"], s["nadir_share"], s["rtk_share"]), (53.0, 0.91, 0.91))
        self.assertAlmostEqual(s["bbox"][0], 51.16)                   # the fix 1500 km away is not in the extent
        self.assertAlmostEqual(s["bbox"][2], 51.1609)
