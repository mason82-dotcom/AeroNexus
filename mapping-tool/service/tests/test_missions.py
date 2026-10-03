"""Mission planning logic (app/missions.py): checklists per purpose, status transitions, input validation."""
import json
import unittest
from datetime import datetime, timezone

from pydantic import ValidationError

from app import missions


class ChecklistTest(unittest.TestCase):
    def test_base_and_purpose_items(self):
        base = {c["key"] for c in missions.new_checklist("other")}
        self.assertIn("airspace", base)
        self.assertIn("route", base)
        pv = {c["key"] for c in missions.new_checklist("pv")}
        self.assertEqual(pv - base, {"irradiance", "thermal_settings"})
        self.assertIn("sunlight_sensor", {c["key"] for c in missions.new_checklist("farming")})
        self.assertTrue(all(not c["done"] for c in missions.new_checklist("pv")))

    def test_tick_records_who_and_when(self):
        cl = missions.new_checklist("other")
        missions.tick(cl, "weather", True, "pilot", "2026-10-03T10:00:00Z")
        item = next(c for c in cl if c["key"] == "weather")
        self.assertEqual((item["done"], item["by"], item["at"]), (True, "pilot", "2026-10-03T10:00:00Z"))
        missions.tick(cl, "weather", False, "pilot", "2026-10-03T10:01:00Z")
        self.assertEqual((item["done"], item["by"], item["at"]), (False, None, None))
        with self.assertRaises(KeyError):
            missions.tick(cl, "unknown", True, "pilot", "x")

    def test_purpose_change_keeps_ticked_items(self):
        cl = missions.new_checklist("pv")
        missions.tick(cl, "weather", True, "a", "t")
        missions.tick(cl, "irradiance", True, "a", "t")
        merged = missions.merge_checklist(cl, "farming")
        keys = {c["key"]: c["done"] for c in merged}
        self.assertTrue(keys["weather"])
        self.assertNotIn("irradiance", keys)                      # PV only
        self.assertFalse(keys["sunlight_sensor"])

    def test_auto_status(self):
        cl = missions.new_checklist("other")
        self.assertEqual(missions.auto_status("planned", cl), "planned")
        for c in cl:
            missions.tick(cl, c["key"], True, "a", "t")
        self.assertEqual(missions.auto_status("planned", cl), "ready")
        missions.tick(cl, "batteries", False, "a", "t")
        self.assertEqual(missions.auto_status("ready", cl), "planned")
        self.assertEqual(missions.auto_status("flown", cl), "flown")          # never moves a flown mission


class TransitionTest(unittest.TestCase):
    def test_allowed(self):
        for old, new in (("planned", "flown"), ("ready", "flown"), ("flown", "evaluated"), ("planned", "cancelled"),
                         ("cancelled", "planned"), ("evaluated", "flown"), ("flown", "flown")):
            missions.check_transition(old, new)

    def test_refused(self):
        for old, new in (("planned", "evaluated"), ("cancelled", "flown"), ("evaluated", "planned"), ("planned", "x")):
            with self.subTest((old, new)), self.assertRaises(ValueError):
                missions.check_transition(old, new)


class ModelTest(unittest.TestCase):
    def test_valid_and_utc(self):
        m = missions.MissionIn(title="  PV Scheune ", purpose="pv", planned_start="2026-10-04T11:30:00+02:00",
                               wayline_ids=["a", "b"])
        self.assertEqual(m.title, "PV Scheune")
        self.assertEqual(missions._utc(m.planned_start), datetime(2026, 10, 4, 9, 30))

    def test_invalid(self):
        for bad in ({"title": "", "purpose": "pv"}, {"title": "x", "purpose": "fishing"},
                    {"title": "x", "purpose": "pv", "duration_min": 0},
                    {"title": "x", "purpose": "pv", "wayline_ids": [str(i) for i in range(21)]}):
            with self.subTest(bad), self.assertRaises(ValidationError):
                missions.MissionIn(planned_start="2026-10-04T10:00:00Z", **bad)

    def test_output_row(self):
        row = {"id": "m1", "title": "T", "purpose": "pv", "planned_start": datetime(2026, 10, 4, 9, 30),
               "duration_min": 30, "site": "Scheune", "notes": None, "drone_sn": "SN", "pilot": "pilot",
               "wayline_ids": json.dumps(["w1"]), "checklist": json.dumps(missions.new_checklist("pv")),
               "status": "planned", "created_by": "adminPC", "created_at": datetime(2026, 10, 3, 8, 0),
               "updated_at": datetime(2026, 10, 3, 8, 0), "status_at": None}
        out = missions._out(row)
        self.assertEqual(out["planned_start"], "2026-10-04T09:30:00Z")
        self.assertEqual(out["purpose_label"], "PV-Inspektion")
        self.assertEqual(out["wayline_ids"], ["w1"])
        self.assertEqual(out["notes"], "")
        self.assertEqual(datetime.fromisoformat(out["planned_start"].replace("Z", "+00:00")).tzinfo, timezone.utc)


if __name__ == "__main__":
    unittest.main()
