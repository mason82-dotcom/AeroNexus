#!/usr/bin/env python3
"""Simulated compute agent: plays the agent API (docs/agent-api.md) without an x64 node.

Steps: create job (web login) -> claim with short lease -> heartbeat -> let the lease expire ->
check 409 + requeue -> claim again -> upload generated test tiles + manifest -> complete ->
check layer and a tile via the tile redirect.

Standard library only. Reads edge/.env. Usage:
  python3 mapping-tool/service/tools/sim_agent.py [--lease 15] [--lon 8.5905 --lat 49.1575]
"""
import argparse
import hashlib
import json
import math
import struct
import sys
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def load_env() -> dict:
    env = {}
    for line in (ROOT / "edge" / ".env").read_text().splitlines():
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip().strip('"')
    return env


def call(method: str, url: str, body=None, headers=None, raw: bytes | None = None, follow=True):
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if body is not None:
        req.add_header("Content-Type", "application/json")
    opener = urllib.request.build_opener() if follow else urllib.request.build_opener(_NoRedirect())
    try:
        with opener.open(req, timeout=30) as resp:
            payload = resp.read()
            return resp.status, payload, resp.headers
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), exc.headers


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def js(payload: bytes):
    return json.loads(payload) if payload else None


def png_rgba(size: int, rgba: tuple, border: tuple) -> bytes:
    rows = []
    for y in range(size):
        row = bytearray([0])
        for x in range(size):
            edge = x < 3 or y < 3 or x >= size - 3 or y >= size - 3
            row += bytes(border if edge else rgba)
        rows.append(bytes(row))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"".join(rows), 9)) + chunk(b"IEND", b""))


def tile_xy(lon: float, lat: float, z: int) -> tuple[int, int]:
    n = 2 ** z
    x = int((lon + 180) / 360 * n)
    y = int((1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)
    return x, y


def tile_bounds(x: int, y: int, z: int) -> tuple[float, float, float, float]:
    n = 2 ** z

    def lat(yy):
        return math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yy / n))))

    return x / n * 360 - 180, lat(y + 1), (x + 1) / n * 360 - 180, lat(y)


def step(msg: str):
    print(f"\n== {msg}")


def check(cond: bool, msg: str):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        sys.exit(1)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lease", type=int, default=15, help="lease seconds of the first claim (>= 10)")
    ap.add_argument("--lon", type=float, default=8.5905)
    ap.add_argument("--lat", type=float, default=49.1575)
    ap.add_argument("--images", type=int, default=2, help="number of RGB images for the job")
    args = ap.parse_args()

    env = load_env()
    host = env["SERVER_HOST"]
    base = f"http://{host}:6790/api/mapping"
    agent_hdr = {"Authorization": f"Bearer {env['MAPPING_AGENT_TOKEN']}"}

    step("web login (adminPC) and job creation")
    status, payload, _ = call("POST", f"http://{host}:6789/manage/api/v1/login",
                              {"username": "adminPC", "password": env["WEB_ADMIN_PASSWORD"], "flag": 1})
    token = js(payload)["data"]["access_token"]
    web_hdr = {"x-auth-token": token}
    status, payload, _ = call("GET", f"{base}/media?page_size=500", headers=web_hdr)
    media = js(payload)["list"]
    rgb = [m["object_key"] for m in media if m["file_name"].upper().endswith(("_D.JPG", "_V.JPG", "_W.JPG"))]
    check(len(rgb) >= 1, f"{len(rgb)} RGB image(s) in the media library")
    status, payload, _ = call("POST", f"{base}/jobs", {"name": "Simulierter Agent", "image_keys": rgb[:args.images],
                                                        "profile": "fast"}, web_hdr)
    job = js(payload)
    check(status == 201 and job["status"] == "QUEUED", f"job {job['id']} QUEUED")
    job_id = job["id"]

    step(f"claim by sim-1 with lease {args.lease}s")
    status, payload, _ = call("POST", f"{base}/agent/claim",
                              {"agent_id": "sim-1", "lease_seconds": args.lease,
                               "capabilities": {"ram_gb": 0, "gpu": None, "engine": "simulated"}}, agent_hdr)
    claim = js(payload)
    check(status == 200 and claim["job"]["id"] == job_id, f"claimed {job_id}, lease until {claim['job']['lease_until']}")
    status, _, _ = call("GET", claim["images"][0]["url"])
    check(status == 200, "first input image downloadable via presigned URL")
    status, payload, _ = call("POST", f"{base}/agent/jobs/{job_id}/heartbeat",
                              {"agent_id": "sim-1", "progress": 5, "message": "downloading",
                               "lease_seconds": args.lease}, agent_hdr)
    check(status == 200 and js(payload)["status"] == "RUNNING", "heartbeat -> RUNNING")

    step(f"sim-1 stops sending heartbeats, waiting {args.lease + 3}s for the lease to expire")
    time.sleep(args.lease + 3)
    status, _, _ = call("POST", f"{base}/agent/jobs/{job_id}/heartbeat", {"agent_id": "sim-1"}, agent_hdr)
    check(status == 409, f"late heartbeat of sim-1 rejected ({status})")
    status, payload, _ = call("GET", f"{base}/jobs/{job_id}", headers=web_hdr)
    job = js(payload)
    check(job["status"] == "QUEUED" and job["attempts"] == 1, f"job back to QUEUED, attempts={job['attempts']}")

    step("claim by sim-2")
    status, payload, _ = call("POST", f"{base}/agent/claim", {"agent_id": "sim-2", "lease_seconds": 300}, agent_hdr)
    check(status == 200 and js(payload)["job"]["id"] == job_id, "same job claimed again by sim-2")
    call("POST", f"{base}/agent/jobs/{job_id}/heartbeat",
         {"agent_id": "sim-2", "progress": 80, "message": "tiling"}, agent_hdr)

    step("generate and upload test tiles")
    tiles: dict[str, bytes] = {}
    minzoom, maxzoom = 15, 18
    west = south = 180.0
    east = north = -180.0
    for z in range(minzoom, maxzoom + 1):
        cx, cy = tile_xy(args.lon, args.lat, z)
        radius = 1 if z < 17 else 2
        for x in range(cx - radius, cx + radius + 1):
            for y in range(cy - radius, cy + radius + 1):
                color = (255, 140, 0, 110) if (x + y) % 2 else (0, 160, 255, 110)
                tiles[f"tiles/{z}/{x}/{y}.png"] = png_rgba(256, color, (255, 255, 255, 220))
                if z == maxzoom:
                    w, s, e, n = tile_bounds(x, y, z)
                    west, south, east, north = min(west, w), min(south, s), max(east, e), max(north, n)
    manifest = {
        "crs": "EPSG:3857",
        "bounds_wgs84": [round(west, 7), round(south, 7), round(east, 7), round(north, 7)],
        "files": [],
        "tiles": {"path": "tiles", "format": "png", "minzoom": minzoom, "maxzoom": maxzoom},
    }
    manifest_bytes = json.dumps(manifest, indent=2).encode()
    manifest["files"].append({"path": "manifest.json", "kind": "other",
                              "sha256": hashlib.sha256(manifest_bytes).hexdigest(), "size": len(manifest_bytes)})
    uploads = {**tiles, "manifest.json": manifest_bytes}
    status, payload, _ = call("POST", f"{base}/agent/jobs/{job_id}/upload-urls",
                              {"agent_id": "sim-2", "paths": list(uploads)}, agent_hdr)
    urls = js(payload)["urls"]
    check(status == 200 and len(urls) == len(uploads), f"{len(urls)} presigned PUT URLs")
    failed = [p for p, data in uploads.items()
              if call("PUT", urls[p], raw=data, headers={"Content-Type": "application/octet-stream"})[0] != 200]
    check(not failed, f"{len(uploads)} files uploaded to mapping-results/{job_id}/")

    step("complete")
    status, payload, _ = call("POST", f"{base}/agent/jobs/{job_id}/complete",
                              {"agent_id": "sim-2", "manifest": manifest}, agent_hdr)
    result = js(payload)
    check(status == 200 and result["status"] == "DONE", f"job DONE, layer {result.get('layer_id')}")

    step("layer visible for the web UI")
    status, payload, _ = call("GET", f"{base}/layers", headers=web_hdr)
    layer = next((l for l in js(payload) if l["id"] == result["layer_id"]), None)
    check(layer is not None, f"layer '{layer['name']}' listed, zoom {layer['min_zoom']}-{layer['max_zoom']}")
    cx, cy = tile_xy(args.lon, args.lat, maxzoom)
    tile_url = f"http://{host}:6790" + layer["tile_url"].format(z=maxzoom, x=cx, y=cy) + f"?token={token}"
    status, _, headers = call("GET", tile_url, follow=False)
    check(status == 302 and f"{host}:9000" in headers.get("Location", ""), "tile endpoint redirects to MinIO on SERVER_HOST")
    status, payload, headers = call("GET", tile_url)
    check(status == 200 and payload[:8] == b"\x89PNG\r\n\x1a\n", f"tile {maxzoom}/{cx}/{cy} delivered as PNG ({len(payload)} bytes)")
    print(f"\nDone. Job {job_id}, layer {layer['id']}. In the web UI: Mapping -> Result layers -> switch on.")


if __name__ == "__main__":
    main()
