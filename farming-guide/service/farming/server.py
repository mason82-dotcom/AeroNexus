"""AeroNexus Farming Guide service: zone maps from vegetation index layers and exports.

Standard library HTTP server (no extra dependencies in the pinned GDAL image). Routes are registered
with @route; handlers get (request, user, **path_params) and return (status, body[, content_type]).
"""
import json
import logging
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from . import auth, config

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("farming")
settings = config.load()
ROUTES: list[tuple[str, re.Pattern, object, bool]] = []
MAX_BODY = 1024 * 1024


class HttpError(Exception):
    def __init__(self, status: int, detail):
        super().__init__(str(detail))
        self.status, self.detail = status, detail


def route(method: str, pattern: str, public: bool = False):
    regex = re.compile("^" + re.sub(r"{(\w+)}", r"(?P<\1>[A-Za-z0-9_-]+)", pattern) + "$")

    def wrap(fn):
        ROUTES.append((method, regex, fn, public))
        return fn
    return wrap


class Request:
    def __init__(self, handler: BaseHTTPRequestHandler):
        self.handler = handler
        parsed = urlparse(handler.path)
        self.path = parsed.path
        self.query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
        self.headers = handler.headers
        self._body = None

    def json(self) -> dict:
        if self._body is None:
            length = int(self.headers.get("Content-Length") or 0)
            if length > MAX_BODY:
                raise HttpError(413, "request too large")
            raw = self.handler.rfile.read(length) if length else b""
            try:
                self._body = json.loads(raw or b"{}")
            except json.JSONDecodeError as exc:
                raise HttpError(422, "invalid JSON") from exc
        if not isinstance(self._body, dict):
            raise HttpError(422, "JSON object expected")
        return self._body

    @property
    def token(self) -> str | None:
        # header for API calls, query parameter for <a href> downloads
        return self.headers.get("x-auth-token") or self.query.get("token")


class Handler(BaseHTTPRequestHandler):
    server_version = "AeroNexusFarming/1"

    def _cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "x-auth-token, content-type")

    def _send(self, status: int, body, content_type: str = "application/json", headers: dict | None = None):
        data = body if isinstance(body, (bytes, bytearray)) else json.dumps(body).encode()
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(data)

    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _dispatch(self, method: str):
        req = Request(self)
        for m, regex, fn, public in ROUTES:
            match = regex.match(req.path)
            if m != method or not match:
                continue
            try:
                user = None if public else auth.decode(req.token, settings.jwt_secret)
                result = fn(req, user, **match.groupdict())
                self._send(*result) if isinstance(result, tuple) else self._send(200, result)
            except auth.AuthError as exc:
                self._send(401, {"detail": str(exc)})
            except HttpError as exc:
                self._send(exc.status, {"detail": exc.detail})
            except Exception:
                log.error("%s %s failed:\n%s", method, req.path, traceback.format_exc())
                self._send(500, {"detail": "internal error, see service log"})
            return
        self._send(404, {"detail": "not found"})

    def do_GET(self):
        self._dispatch("GET")

    def do_POST(self):
        self._dispatch("POST")

    def do_PUT(self):
        self._dispatch("PUT")

    def do_DELETE(self):
        self._dispatch("DELETE")

    def log_message(self, fmt, *args):
        # never log the login token of download links
        log.info("%s %s", self.address_string(), re.sub(r"token=[^& ]+", "token=***", fmt % args))


@route("GET", "/health", public=True)
def health(req, user):
    return {"status": "ok"}


def serve():
    srv = ThreadingHTTPServer(("0.0.0.0", settings.port), Handler)
    log.info("farming service on :%d", settings.port)
    srv.serve_forever()
