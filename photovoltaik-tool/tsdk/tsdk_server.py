"""Minimal HTTP wrapper around the DJI Thermal SDK tool dji_irp (only reachable inside the compose network).

  GET  /health                         {"sdk": true|false, "runner": "native"|"box64", "version": "..."}
  POST /measure?distance=&humidity=&emissivity=&reflection=   body: R-JPEG -> float32 deg C, 640 x 512
"""
import json
import os
import platform
import subprocess
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SDK_BIN = Path(os.environ.get("TSDK_BIN", "/opt/dji-tsdk/bin"))
PORT = int(os.environ.get("PORT", "6793"))
MAX_BODY = 30 * 1024 * 1024
RANGES = {"distance": (1.0, 25.0, 5.0), "humidity": (20.0, 100.0, 70.0),
          "emissivity": (0.1, 1.0, 0.95), "reflection": (-40.0, 500.0, 23.0)}   # dji_irp argument ranges
_slots = threading.Semaphore(2)          # the SDK is single image; 2 parallel conversions on 4 cores


def runner() -> list[str]:
    return [] if platform.machine() in ("x86_64", "amd64") else ["box64"]


def env() -> dict:
    e = dict(os.environ, LD_LIBRARY_PATH=str(SDK_BIN))
    e["BOX64_LD_LIBRARY_PATH"] = f"{SDK_BIN}:/opt/x86libs"
    return e


def tool() -> Path | None:
    p = SDK_BIN / "dji_irp"
    return p if p.is_file() else None


def version() -> str:
    t = tool()
    if not t:
        return ""
    try:
        out = subprocess.run([*runner(), str(t), "-V"], capture_output=True, text=True, env=env(), timeout=60)
        lines = [l for l in (out.stdout + out.stderr).splitlines() if "version" in l.lower() and "BOX64" not in l]
        return lines[-1].split(":", 1)[-1].strip() if lines else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def measure(jpeg: bytes, args: dict) -> bytes:
    t = tool()
    if not t:
        raise RuntimeError("DJI Thermal SDK fehlt (photovoltaik-tool/tools/install-tsdk.sh)")
    vals = {}
    for k, (lo, hi, default) in RANGES.items():
        try:
            v = float(args.get(k, default))
        except ValueError:
            v = default
        vals[k] = min(max(v, lo), hi)
    with _slots, tempfile.TemporaryDirectory() as tmp:
        src, out = Path(tmp) / "in.jpg", Path(tmp) / "out.raw"
        src.write_bytes(jpeg)
        cmd = [*runner(), str(t), "-s", str(src), "-a", "measure", "-o", str(out), "--measurefmt", "float32",
               "--distance", f"{vals['distance']:.1f}", "--humidity", f"{vals['humidity']:.0f}",
               "--emissivity", f"{vals['emissivity']:.2f}", "--reflection", f"{vals['reflection']:.1f}"]
        proc = subprocess.run(cmd, capture_output=True, text=True, cwd=tmp, env=env(), timeout=120)
        if proc.returncode != 0 or not out.exists():
            msg = (proc.stderr or proc.stdout).strip().splitlines()
            raise RuntimeError("dji_irp: " + (msg[-1] if msg else f"exit {proc.returncode}"))
        return out.read_bytes()


class Handler(BaseHTTPRequestHandler):
    server_version = "AeroNexusTSDK/1"

    def _send(self, status: int, body: bytes, ctype: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path == "/health":
            info = {"sdk": tool() is not None, "runner": "native" if not runner() else "box64",
                    "version": VERSION}
            return self._send(200, json.dumps(info).encode())
        self._send(404, b'{"detail": "not found"}')

    def do_POST(self):
        url = urlparse(self.path)
        if url.path != "/measure":
            return self._send(404, b'{"detail": "not found"}')
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return self._send(413, b'{"detail": "image missing or too large"}')
        jpeg = self.rfile.read(length)
        try:
            data = measure(jpeg, {k: v[0] for k, v in parse_qs(url.query).items()})
        except (RuntimeError, subprocess.SubprocessError) as exc:
            return self._send(422, json.dumps({"detail": str(exc)}).encode())
        self._send(200, data, "application/octet-stream")

    def log_message(self, fmt, *args):
        pass                                # one line per image would flood the log


VERSION = version()

if __name__ == "__main__":
    print(f"tsdk helper on :{PORT}, sdk={'yes ' + VERSION if tool() else 'missing'}, "
          f"runner={'native' if not runner() else 'box64'}", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
