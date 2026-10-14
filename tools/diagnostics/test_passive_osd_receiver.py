"""Offline tests for passive OSD receiver; no MQTT broker connection."""
import importlib.util
from pathlib import Path
import sys
import unittest

root = Path(__file__).parent
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("passive_osd_receiver", root / "passive_osd_receiver.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
PassiveOsdReceiver = module.PassiveOsdReceiver


class ReceiverTests(unittest.TestCase):
    def test_valid_osd_updates_freshness(self):
        now = [100.0]
        receiver = PassiveOsdReceiver(clock=lambda: now[0])
        self.assertTrue(receiver.accept("thing/product/aircraft/osd", b'{"data":{"latitude":12}}'))
        now[0] = 111.0
        self.assertEqual(receiver.status("aircraft", True)["telemetry"], "stale")

    def test_wrong_topic_rejected(self):
        receiver = PassiveOsdReceiver()
        self.assertFalse(receiver.accept("sys/product/aircraft/status", b'{"data":{"x":1}}'))
        self.assertFalse(receiver.accept("thing/product/a/state", b'{"data":{"x":1}}'))

    def test_invalid_json_rejected(self):
        receiver = PassiveOsdReceiver()
        self.assertFalse(receiver.accept("thing/product/a/osd", b"{invalid"))
        self.assertFalse(receiver.accept("thing/product/a/osd", b"[]"))
        self.assertFalse(receiver.accept("thing/product/a/osd", b'{"data":{}}'))

    def test_large_payload_rejected(self):
        receiver = PassiveOsdReceiver()
        self.assertFalse(receiver.accept("thing/product/a/osd", b"x" * 65537))

    def test_non_utf8_rejected(self):
        receiver = PassiveOsdReceiver()
        self.assertFalse(receiver.accept("thing/product/a/osd", b"\\xff"))

    def test_device_isolation(self):
        receiver = PassiveOsdReceiver(clock=lambda: 100)
        receiver.accept("thing/product/a/osd", b'{"data":{"x":1}}')
        self.assertEqual(receiver.status("b", True)["status"], "unknown")

    def test_payload_not_retained(self):
        receiver = PassiveOsdReceiver(clock=lambda: 100)
        receiver.accept("thing/product/a/osd", b'{"data":{"secret":"sensitive"}}')
        self.assertFalse(hasattr(receiver, "payload"))
        self.assertEqual(receiver.accepted, 1)
        self.assertEqual(receiver.rejected, 0)


if __name__ == "__main__":
    unittest.main()
