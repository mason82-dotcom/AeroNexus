"""Offline regression tests for OSD freshness; no live MQTT subscription."""
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location(
    "osd_freshness", Path(__file__).with_name("osd_freshness.py")
)
module = importlib.util.module_from_spec(spec)
import sys
sys.modules[spec.name] = module
spec.loader.exec_module(module)
OsdFreshness = module.OsdFreshness


class FreshnessTests(unittest.TestCase):
    def test_active_after_receipt(self):
        monitor = OsdFreshness(10)
        monitor.record("aircraft", 100)
        self.assertEqual(monitor.status("aircraft", 105, True)["telemetry"], "active")

    def test_stale_after_threshold(self):
        monitor = OsdFreshness(10)
        monitor.record("aircraft", 100)
        self.assertEqual(monitor.status("aircraft", 110, True)["telemetry"], "active")
        self.assertEqual(monitor.status("aircraft", 110.01, True)["telemetry"], "stale")

    def test_connected_without_osd_is_unknown(self):
        self.assertEqual(OsdFreshness().status("aircraft", 100, True)["status"], "unknown")

    def test_offline_overrides_last_seen(self):
        monitor = OsdFreshness()
        monitor.record("aircraft", 100)
        self.assertEqual(monitor.status("aircraft", 101, False)["telemetry"], "inactive")

    def test_unknown_connection_never_claims_active(self):
        monitor = OsdFreshness()
        monitor.record("aircraft", 100)
        self.assertEqual(monitor.status("aircraft", 101, None)["status"], "unknown")

    def test_devices_are_isolated(self):
        monitor = OsdFreshness()
        monitor.record("a", 100)
        self.assertEqual(monitor.status("b", 101, True)["status"], "unknown")

    def test_out_of_order_receipts_do_not_regress(self):
        monitor = OsdFreshness()
        monitor.record("a", 100)
        monitor.record("a", 90)
        self.assertEqual(monitor.last_seen["a"], 100)

    def test_invalid_inputs(self):
        monitor = OsdFreshness()
        with self.assertRaises(ValueError):
            monitor.record("", 10)
        with self.assertRaises(ValueError):
            monitor.record("a", float("nan"))
        with self.assertRaises(ValueError):
            OsdFreshness(0)
        with self.assertRaises(ValueError):
            monitor.status("a", float("inf"), True)

    def test_future_timestamp_not_considered_fresh(self):
        monitor = OsdFreshness()
        monitor.record("a", 200)
        self.assertEqual(monitor.status("a", 100, True)["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
