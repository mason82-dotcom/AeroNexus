"""HS256 JWT of the DJI backend (same check as the mapping service), standard library only."""
import base64
import hashlib
import hmac
import json
import time


class AuthError(Exception):
    pass


def _b64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def decode(token: str | None, secret: str) -> dict:
    if not token:
        raise AuthError("token missing")
    try:
        header_b64, payload_b64, sig_b64 = token.split(".")
        header = json.loads(_b64(header_b64))
        payload = json.loads(_b64(payload_b64))
    except (ValueError, json.JSONDecodeError) as exc:
        raise AuthError("malformed token") from exc
    if header.get("alg") != "HS256":
        raise AuthError("unsupported algorithm")
    expected = hmac.new(secret.encode(), f"{header_b64}.{payload_b64}".encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(expected, _b64(sig_b64)):
        raise AuthError("bad signature")
    if payload.get("iss") != "DJI" or "exp" not in payload or payload["exp"] < time.time():
        raise AuthError("expired or foreign token")
    if not payload.get("workspace_id"):
        raise AuthError("token without workspace")
    return payload
