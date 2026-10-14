"""Observer configuration tests; no MQTT package, credentials or network required."""
import importlib.util
from pathlib import Path
import sys
import unittest

root = Path(__file__).parent
sys.path.insert(0, str(root))
spec = importlib.util.spec_from_file_location("osd_mqtt_observer", root / "osd_mqtt_observer.py")
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)


class ObserverConfigurationTests(unittest.TestCase):
    def test_missing_password_rejected(self):
        with self.assertRaises(ValueError):
            module.configuration({})

    def test_missing_username_rejected(self):
        with self.assertRaises(ValueError):
            module.configuration({"AERONEXUS_OSD_PASSWORD": "test"})

    def test_companion_account_rejected(self):
        with self.assertRaises(ValueError):
            module.configuration({"AERONEXUS_OSD_USERNAME": "companion", "AERONEXUS_OSD_PASSWORD": "test"})

    def test_privileged_accounts_rejected(self):
        for username in ("backend", "admin", "dashboard"):
            with self.subTest(username=username), self.assertRaises(ValueError):
                module.configuration({"AERONEXUS_OSD_USERNAME": username, "AERONEXUS_OSD_PASSWORD": "test"})

    def test_defaults(self):
        self.assertEqual(module.configuration({"AERONEXUS_OSD_USERNAME": "osd_observer", "AERONEXUS_OSD_PASSWORD": "test"}),
                         ("127.0.0.1", 1884, "osd_observer", "test"))

    def test_explicit_host_port(self):
        self.assertEqual(module.configuration({
            "AERONEXUS_OSD_USERNAME": "osd_observer",
            "AERONEXUS_OSD_PASSWORD": "test",
            "AERONEXUS_OSD_HOST": "broker.internal",
            "AERONEXUS_OSD_PORT": "1883",
        }), ("broker.internal", 1883, "osd_observer", "test"))

    def test_invalid_port_rejected(self):
        with self.assertRaises(ValueError):
            module.configuration({"AERONEXUS_OSD_USERNAME": "osd_observer", "AERONEXUS_OSD_PASSWORD": "test", "AERONEXUS_OSD_PORT": "65536"})

    def test_invalid_host_rejected(self):
        with self.assertRaises(ValueError):
            module.configuration({"AERONEXUS_OSD_USERNAME": "osd_observer", "AERONEXUS_OSD_PASSWORD": "test", "AERONEXUS_OSD_HOST": "bad host"})


if __name__ == "__main__":
    unittest.main()
