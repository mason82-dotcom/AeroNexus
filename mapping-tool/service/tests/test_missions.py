"""Mission planning logic (app/missions.py): checklists per purpose, status transitions, input validation."""
import json
import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

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



class MatchTest(unittest.TestCase):
    TZ = ZoneInfo("Europe/Berlin")

    def fs(self, sid, drone, first, last):
        return {"id": sid, "drone": drone, "first_capture": first, "last_capture": last}

    def test_local_camera_time_same_day_and_drone(self):
        # mission 12:00 local (10:00 UTC, CEST), 30 min; camera clocks run local
        sets = [self.fs("late", "SN", datetime(2026, 10, 4, 17, 25), datetime(2026, 10, 4, 17, 29)),
                self.fs("exact", "SN", datetime(2026, 10, 4, 12, 5), datetime(2026, 10, 4, 12, 20)),
                self.fs("other drone", "XX", datetime(2026, 10, 4, 12, 5), datetime(2026, 10, 4, 12, 20)),
                self.fs("next day", "SN", datetime(2026, 10, 5, 0, 10), datetime(2026, 10, 5, 0, 20)),
                self.fs("utc trap", "SN", datetime(2026, 10, 4, 10, 0), datetime(2026, 10, 4, 10, 10))]
        hits = missions.match_flight_sets(datetime(2026, 10, 4, 10, 0), 30, "SN", sets, self.TZ)
        self.assertEqual([h["id"] for h in hits], ["exact", "utc trap", "late"])
        self.assertEqual([h["gap_min"] for h in hits], [0, 110, 295])     # 10:10 local ends 110 min before 12:00

    def test_margin_and_without_drone(self):
        sets = [self.fs("early", "A", datetime(2026, 10, 4, 1, 0), datetime(2026, 10, 4, 1, 30)),
                self.fs("noon", "B", datetime(2026, 10, 4, 13, 0), datetime(2026, 10, 4, 13, 30))]
        hits = missions.match_flight_sets(datetime(2026, 10, 4, 10, 0), None, None, sets, self.TZ)
        self.assertEqual([h["id"] for h in hits], ["noon"])                # 01:30 is 10.5 h before: too far

    def test_output_has_flight_sets(self):
        row = {"id": "m1", "title": "T", "purpose": "pv", "planned_start": datetime(2026, 10, 4, 9, 30),
               "duration_min": None, "site": "", "notes": "", "drone_sn": None, "pilot": None,
               "wayline_ids": "[]", "flight_set_ids": None, "checklist": "[]", "status": "planned",
               "created_by": "a", "created_at": datetime(2026, 10, 3), "updated_at": datetime(2026, 10, 3),
               "status_at": None}
        self.assertEqual(missions._out(row)["flight_set_ids"], [])
        row["flight_set_ids"] = '["s1"]'
        self.assertEqual(missions._out(row)["flight_set_ids"], ["s1"])

    def test_flight_sets_input(self):
        with self.assertRaises(ValidationError):
            missions.FlightSetsIn(flight_set_ids=[str(i) for i in range(21)])


if __name__ == "__main__":
    unittest.main()
