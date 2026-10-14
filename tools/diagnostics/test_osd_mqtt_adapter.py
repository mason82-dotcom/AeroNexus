"""Offline transport contract tests: fake MQTT client, no broker connection."""
import importlib.util
from pathlib import Path
import sys
import types
import unittest

root = Path(__file__).parent
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("osd_mqtt_adapter", root / "osd_mqtt_adapter.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
Adapter = module.ReadOnlyOsdMqttAdapter


class FakeClient:
    def __init__(self, result=(0, 1)):
        self.calls = []
        self.result = result

    def subscribe(self, topic, qos=0):
        self.calls.append((topic, qos))
        return self.result


class AdapterTests(unittest.TestCase):
    def test_subscribes_to_osd_only(self):
        client = FakeClient()
        adapter = Adapter()
        adapter.on_connect(client, None, None, 0)
        self.assertEqual(client.calls, [("thing/product/+/osd", 0)])
        self.assertTrue(adapter.subscribed)

    def test_rejected_connection_does_not_subscribe(self):
        client = FakeClient()
        adapter = Adapter()
        adapter.on_connect(client, None, None, 5)
        self.assertEqual(client.calls, [])
        self.assertFalse(adapter.subscribed)

    def test_failed_subscription_not_connected(self):
        adapter = Adapter()
        adapter.on_connect(FakeClient((1, 0)), None, None, 0)
        self.assertFalse(adapter.subscribed)

    def test_messages_ignored_before_subscription(self):
        adapter = Adapter()
        msg = types.SimpleNamespace(topic="thing/product/a/osd", payload=b'{"data":{"x":1}}')
        adapter.on_message(None, None, msg)
        self.assertEqual(adapter.receiver.accepted, 0)

    def test_disconnect_invalidates_active(self):
        adapter = Adapter()
        adapter.receiver.clock = lambda: 100
        adapter.on_connect(FakeClient(), None, None, 0)
        msg = types.SimpleNamespace(topic="thing/product/a/osd", payload=b'{"data":{"x":1}}')
        adapter.on_message(None, None, msg)
        self.assertEqual(adapter.status("a", True)["telemetry"], "active")
        adapter.on_disconnect(None, None)
        self.assertEqual(adapter.status("a", True)["telemetry"], "unverified")


if __name__ == "__main__":
    unittest.main()
