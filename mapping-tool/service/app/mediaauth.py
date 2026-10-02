"""External authentication of MediaMTX (authMethod: http, telemetry/mediamtx/mediamtx.yml).

MediaMTX posts {user, password, token, ip, action, path, protocol, query} for every publish/read and
expects 2xx (allow) or anything else (deny). Rules:
  publish  Pilot 2 with the publish credentials from the RTMP/WHIP URL the backend hands out
  read     browser with a valid web login (Authorization: Bearer <JWT>, native HLS: ?jwt=<JWT>)
           or RTSP/RTMP readers (VLC) with RTSP_USER / RTSP_PASSWORD
  api, metrics, pprof, playback  only from the server itself
"""
import hmac
import logging
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Response
from pydantic import BaseModel

from .auth import decode_user
from .config import Settings

log = logging.getLogger("mapping.mediaauth")
LOCAL_IPS = {"127.0.0.1", "::1"}


class MediaAuthRequest(BaseModel):
    user: str = ""
    password: str = ""
    token: str = ""
    ip: str = ""
    action: str = ""
    path: str = ""
    protocol: str = ""
    query: str = ""


def _same(a: str, b: str) -> bool:
    return bool(a) and bool(b) and hmac.compare_digest(a.encode(), b.encode())


def decide(settings: Settings, req: MediaAuthRequest) -> tuple[bool, str]:
    """(allowed, reason) - reason is logged, never returned to the client."""
    if req.action == "publish":
        if not req.path.startswith("live/"):
            return False, "publish outside live/"
        if req.user == settings.live_publish_user and _same(req.password, settings.live_publish_password):
            return True, "publisher"
        return False, "wrong publish credentials"
    if req.action == "read":
        if req.user and req.protocol in ("rtsp", "rtmp"):
            if req.user == settings.rtsp_user and _same(req.password, settings.rtsp_password):
                return True, "rtsp reader"
            return False, "wrong reader credentials"
        token = req.token or (parse_qs(req.query).get("jwt") or [""])[0]
        if not token:
            return False, "no login"
        try:
            user = decode_user(settings, token)
        except HTTPException:
            return False, "invalid login"
        return True, f"web user {user.username}"
    if req.action in ("api", "metrics", "pprof", "playback"):
        return (req.ip in LOCAL_IPS), "local admin" if req.ip in LOCAL_IPS else "admin action from LAN"
    return False, f"unknown action {req.action}"


def build_router(settings: Settings) -> APIRouter:
    router = APIRouter()

    @router.post("/internal/mediamtx-auth", include_in_schema=False)
    def mediamtx_auth(body: MediaAuthRequest) -> Response:
        allowed, reason = decide(settings, body)
        if not allowed:
            log.info("livestream %s %s/%s from %s denied: %s", body.action, body.protocol, body.path, body.ip, reason)
        return Response(status_code=204 if allowed else 401)

    return router
