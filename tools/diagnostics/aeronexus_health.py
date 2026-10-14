#!/usr/bin/env python3
"""Read-only AeroNexus infrastructure health report (JSON to stdout)."""
import json
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

PROJECT = "aeronexus-edge"
SERVICES = ("control", "mapping", "pv", "mediamtx", "farming", "api",
            "emqx", "mysql", "redis", "tsdk", "minio")
HTTP = {
    "control": "http://127.0.0.1:8085/",
    "mapping": "http://127.0.0.1:6790/health",
    "farming": "http://127.0.0.1:6791/health",
    "pv": "http://127.0.0.1:6792/health",
    "minio": "http://127.0.0.1:9000/minio/health/live",
}
TCP = {
    "api": ("127.0.0.1", 6789),
    "mqtt": ("127.0.0.1", 1884),
    "emqx_dashboard": ("127.0.0.1", 18083),
}


def docker_checks():
    result = subprocess.run(
        ["docker", "ps", "-aq", "--filter",
         f"label=com.docker.compose.project={PROJECT}"],
        capture_output=True, text=True, timeout=10, check=True,
    )
    ids = result.stdout.split()
    if not ids:
        return {}
    result = subprocess.run(
        ["docker", "inspect", *ids],
        capture_output=True, text=True, timeout=15, check=True,
    )
    found = {}
    for container in json.loads(result.stdout):
        labels = container.get("Config", {}).get("Labels") or {}
        service = labels.get("com.docker.compose.service")
        if service not in SERVICES:
            continue
        state = container.get("State") or {}
        health = (state.get("Health") or {}).get("Status")
        running = state.get("Running", False)
        restarts = container.get("RestartCount", 0)
        status = "error" if not running or health == "unhealthy" else (
            "warning" if health == "starting" or restarts else "ok"
        )
        found[service] = {
            "status": status,
            "running": running,
            "health": health or "not_configured",
            "restarts": restarts,
        }
    return found


def http_check(url):
    start = time.monotonic()
    try:
        request = urllib.request.Request(
            url, headers={"User-Agent": "AeroNexus-Health/1.0"}
        )
        with urllib.request.urlopen(request, timeout=4) as response:
            code = response.status
        return {
            "status": "ok" if 200 <= code < 300 else "error",
            "http_code": code,
            "latency_ms": round((time.monotonic() - start) * 1000),
        }
    except urllib.error.HTTPError as exc:
        return {"status": "error", "http_code": exc.code}
    except (OSError, TimeoutError, ValueError) as exc:
        return {"status": "error", "error": type(exc).__name__}


def tcp_check(host, port):
    start = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=4):
            pass
        return {
            "status": "ok",
            "port": port,
            "latency_ms": round((time.monotonic() - start) * 1000),
        }
    except OSError as exc:
        return {"status": "error", "port": port,
                "error": type(exc).__name__}


def tsdk_check():
    """Query the internal thermal SDK health endpoint without exposing a port."""
    command = [
        "docker", "exec", f"{PROJECT}-tsdk-1", "python3", "-c",
        "import urllib.request,sys; "
        "sys.stdout.write(urllib.request.urlopen("
        "'http://127.0.0.1:6793/health', timeout=4).read(4096).decode())",
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=8, check=True
        )
        payload = json.loads(result.stdout)
        if not isinstance(payload, dict) or not isinstance(payload.get("sdk"), bool):
            return {"status": "error", "reason": "invalid_health_response"}
        return {
            "status": "ok" if payload["sdk"] else "warning",
            "sdk_installed": payload["sdk"],
            "runner": payload.get("runner", "unknown"),
            "version": payload.get("version", ""),
        }
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        return {"status": "error", "error": type(exc).__name__}


def build_report():
    checks = {}
    try:
        containers = docker_checks()
        for service in SERVICES:
            checks[f"docker.{service}"] = containers.get(
                service, {"status": "error", "error": "container_missing"}
            )
    except (OSError, subprocess.SubprocessError, ValueError) as exc:
        checks["docker.engine"] = {
            "status": "error", "error": type(exc).__name__
        }

    for name, url in HTTP.items():
        checks[f"http.{name}"] = http_check(url)
    for name, (host, port) in TCP.items():
        checks[f"tcp.{name}"] = tcp_check(host, port)
    checks["tsdk.sdk"] = tsdk_check()
    checks["dji.connection"] = {
        "status": "unknown",
        "reason": "telemetry_probe_not_implemented",
    }
    counts = {
        state: sum(item["status"] == state for item in checks.values())
        for state in ("ok", "warning", "error", "unknown")
    }
    overall = ("error" if counts["error"] else
               "warning" if counts["warning"] else "ok")
    return {
        "schema_version": 1,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "project": PROJECT,
        "checks": checks,
        "summary": counts,
        "overall_status": overall,
    }


def main():
    report = build_report()
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 2 if report["overall_status"] == "error" else (
        1 if report["overall_status"] == "warning" else 0
    )


if __name__ == "__main__":
    sys.exit(main())
