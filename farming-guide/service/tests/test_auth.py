"""Standard library JWT check of the farming service (farming/auth.py): same rules as the mapping service."""
import base64
import hashlib
import hmac
import json
import time
import unittest

from farming import auth

SECRET = "test-secret-0123456789abcdef0123456789"


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def token(secret: str = SECRET, header: dict | None = None, **claims) -> str:
    payload = {"iss": "DJI", "exp": int(time.time()) + 600, "workspace_id": "ws-1", "username": "adminPC"}
    payload.update(claims)
    payload = {k: v for k, v in payload.items() if v is not None}
    h = b64(json.dumps(header or {"typ": "JWT", "alg": "HS256"}).encode())
    p = b64(json.dumps(payload).encode())
    sig = b64(hmac.new(secret.encode(), f"{h}.{p}".encode(), hashlib.sha256).digest())
    return f"{h}.{p}.{sig}"


class DecodeTest(unittest.TestCase):
    def test_valid(self):
        claims = auth.decode(token(), SECRET)
        self.assertEqual((claims["workspace_id"], claims["username"]), ("ws-1", "adminPC"))

    def test_rejected(self):
        valid = token()
        h, p, s = valid.split(".")
        forged_payload = b64(json.dumps({"iss": "DJI", "exp": int(time.time()) + 600,
                                         "workspace_id": "ws-2"}).encode())
        cases = {
            "missing": None,
            "garbage": "not-a-token",
            "foreign secret": token(secret="another-secret-0123456789abcdef0123"),
            "payload swapped": f"{h}.{forged_payload}.{s}",
            "alg none": token(header={"alg": "none"}).rsplit(".", 1)[0] + ".",
            "alg HS512": token(header={"alg": "HS512"}),
            "expired": token(exp=int(time.time()) - 10),
            "no expiry": token(exp=None),
            "foreign issuer": token(iss="someone"),
            "no workspace": token(workspace_id=None),
        }
        for name, tok in cases.items():
            with self.subTest(name), self.assertRaises(auth.AuthError):
                auth.decode(tok, SECRET)


if __name__ == "__main__":
    unittest.main()
