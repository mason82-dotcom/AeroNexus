"""SDK adapter (pv/thermal.py) against a stand-in of the tsdk helper container (HTTP)."""
import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import numpy as np

from pv.thermal import ThermalError, ThermalParams, Tsdk

SEEN = {}


class FakeTsdk(BaseHTTPRequestHandler):
    def do_GET(self):
        self._send(200, json.dumps({"sdk": True, "runner": "box64", "version": "V1.7"}).encode())

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        SEEN.update({k: v[0] for k, v in parse_qs(urlparse(self.path).query).items()}, body=body)
        if body == b"broken":
            return self._send(422, b'{"detail": "dji_irp: unsupported camera"}')
        if body == b"short":
            return self._send(200, np.zeros(10, "<f4").tobytes())
        self._send(200, np.full((512, 640), 31.5, "<f4").tobytes())

    def _send(self, status, data):
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


class TsdkClientTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), FakeTsdk)
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()
        cls.tsdk = Tsdk(f"http://127.0.0.1:{cls.srv.server_port}/")

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()

    def test_health(self):
        self.assertTrue(self.tsdk.available())
        self.assertEqual(self.tsdk.health()["runner"], "box64")
        self.assertFalse(Tsdk("http://127.0.0.1:1").available())          # helper down -> not available

    def test_temperatures_and_parameters(self):
        t = self.tsdk.temperatures(b"jpeg", ThermalParams(emissivity=0.88, reflected_temp=-20, humidity=55), 12.34)
        self.assertEqual(t.shape, (512, 640))
        self.assertAlmostEqual(float(t[0, 0]), 31.5)
        self.assertEqual([float(SEEN[k]) for k in ("emissivity", "reflection", "humidity", "distance")],
                         [0.88, -20.0, 55.0, 12.3])
        self.assertEqual(SEEN["body"], b"jpeg")
        self.tsdk.temperatures(b"jpeg", ThermalParams(distance=7.0), 30.0)
        self.assertEqual(float(SEEN["distance"]), 7.0)                           # fixed distance overrides the image

    def test_errors(self):
        with self.assertRaisesRegex(ThermalError, "unsupported camera"):
            self.tsdk.temperatures(b"broken", ThermalParams(), 5)
        with self.assertRaises(ThermalError):
            self.tsdk.temperatures(b"short", ThermalParams(), 5)
        with self.assertRaisesRegex(ThermalError, "nicht erreichbar"):
            Tsdk("http://127.0.0.1:1").temperatures(b"x", ThermalParams(), 5)

    def test_parameter_ranges_of_the_sdk(self):
        for bad in ({"humidity": 10}, {"emissivity": 0}, {"reflected_temp": 600}):
            with self.subTest(bad), self.assertRaises(ValueError):
                ThermalParams.from_dict(bad)


if __name__ == "__main__":
    unittest.main()
