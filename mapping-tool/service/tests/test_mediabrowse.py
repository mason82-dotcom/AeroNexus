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
