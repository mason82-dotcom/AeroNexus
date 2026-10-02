"""Livestream access rules for MediaMTX (app/mediaauth.py)."""
import time
import unittest
from types import SimpleNamespace

import jwt

from app.mediaauth import MediaAuthRequest, decide

SECRET = "test-secret-0123456789abcdef0123456789"
S = SimpleNamespace(jwt_secret=SECRET, live_publish_user="pilot2cloud", live_publish_password="PubSecret123",
                    rtsp_user="viewer", rtsp_password="RtspSecret456")
LOGIN = jwt.encode({"iss": "DJI", "exp": int(time.time()) + 600, "workspace_id": "ws-1", "username": "adminPC"},
                   SECRET, algorithm="HS256")
EXPIRED = jwt.encode({"iss": "DJI", "exp": int(time.time()) - 10, "workspace_id": "ws-1"}, SECRET, algorithm="HS256")


def req(**kw) -> MediaAuthRequest:
    base = {"ip": "192.168.178.23", "path": "live/1581F6Q8D243100C9A6B-81-0-0", "protocol": "rtmp"}
    return MediaAuthRequest(**{**base, **kw})


class DecideTest(unittest.TestCase):
    def assertAllowed(self, r):
        self.assertTrue(decide(S, r)[0], r)

    def assertDenied(self, r):
        self.assertFalse(decide(S, r)[0], r)

    def test_publish(self):
        self.assertAllowed(req(action="publish", user="pilot2cloud", password="PubSecret123"))
        self.assertDenied(req(action="publish"))                                              # anonymous
        self.assertDenied(req(action="publish", user="pilot2cloud", password="wrong"))
        self.assertDenied(req(action="publish", user="viewer", password="RtspSecret456"))     # reader creds
        self.assertDenied(req(action="publish", token=LOGIN))                                 # web login
        self.assertDenied(req(action="publish", user="pilot2cloud", password="PubSecret123", path="other/x"))

    def test_read_with_web_login(self):
        for protocol in ("webrtc", "hls"):
            self.assertAllowed(req(action="read", protocol=protocol, token=LOGIN))
            self.assertDenied(req(action="read", protocol=protocol))
            self.assertDenied(req(action="read", protocol=protocol, token=EXPIRED))
            self.assertDenied(req(action="read", protocol=protocol, token="garbage"))
        self.assertAllowed(req(action="read", protocol="hls", query=f"jwt={LOGIN}"))         # native HLS (Safari)

    def test_read_with_rtsp_credentials(self):
        self.assertAllowed(req(action="read", protocol="rtsp", user="viewer", password="RtspSecret456"))
        self.assertDenied(req(action="read", protocol="rtsp", user="viewer", password="wrong"))
        self.assertDenied(req(action="read", protocol="rtsp", user="pilot2cloud", password="PubSecret123"))

    def test_empty_configured_password_never_matches(self):
        s = SimpleNamespace(**{**S.__dict__, "rtsp_password": ""})
        self.assertFalse(decide(s, req(action="read", protocol="rtsp", user="viewer", password=""))[0])

    def test_admin_actions_only_local(self):
        for action in ("api", "metrics", "pprof", "playback"):
            self.assertAllowed(req(action=action, ip="127.0.0.1"))
            self.assertDenied(req(action=action))
            self.assertDenied(req(action=action, token=LOGIN))

    def test_unknown_action(self):
        self.assertDenied(req(action="something", token=LOGIN))


if __name__ == "__main__":
    unittest.main()
