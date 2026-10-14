#!/usr/bin/env python3
"""Opt-in, read-only MQTT OSD observer; never publishes or changes broker config.

Requires: python3 -m pip install paho-mqtt
Environment: AERONEXUS_OSD_USERNAME and AERONEXUS_OSD_PASSWORD (mandatory), AERONEXUS_OSD_HOST,
AERONEXUS_OSD_PORT (optional). Use a dedicated broker account with OSD-only ACL.
"""
import argparse
import json
import os
import sys
import time

from osd_mqtt_adapter import ReadOnlyOsdMqttAdapter


def configuration(environ):
    username = environ.get("AERONEXUS_OSD_USERNAME")
    if not username or username.strip() != username or any(ch.isspace() for ch in username):
        raise ValueError("AERONEXUS_OSD_USERNAME must be set to a dedicated account")
    if username in {"companion", "backend", "admin", "dashboard"}:
        raise ValueError("shared or privileged MQTT accounts are not allowed")
    password = environ.get("AERONEXUS_OSD_PASSWORD")
    if not password:
        raise ValueError("AERONEXUS_OSD_PASSWORD must be set")
    host = environ.get("AERONEXUS_OSD_HOST", "127.0.0.1")
    if not host or any(ch.isspace() for ch in host):
        raise ValueError("invalid broker host")
    port = int(environ.get("AERONEXUS_OSD_PORT", "1884"))
    if not 1 <= port <= 65535:
        raise ValueError("broker port out of range")
    return host, port, username, password


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only DJI OSD observer (opt-in)")
    parser.add_argument("--seconds", type=int, default=30, help="Observation duration (1-300 seconds)")
    args = parser.parse_args(argv)
    if not 1 <= args.seconds <= 300:
        parser.error("--seconds must be between 1 and 300")
    try:
        host, port, username, password = configuration(os.environ)
    except ValueError as exc:
        parser.error(str(exc))
    try:
        import paho.mqtt.client as mqtt
    except ImportError:
        parser.error("paho-mqtt is required; install separately")

    adapter = ReadOnlyOsdMqttAdapter()
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id="aeronexus-osd-observer")
    client.username_pw_set(username, password)
    client.on_connect = adapter.on_connect
    client.on_disconnect = adapter.on_disconnect
    client.on_subscribe = adapter.on_subscribe
    client.on_message = adapter.on_message
    try:
        client.connect(host, port, keepalive=30)
        client.loop_start()
        deadline = time.monotonic() + args.seconds
        while time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.0, deadline - time.monotonic())))
    except (OSError, KeyboardInterrupt) as exc:
        print(json.dumps({"status": "unknown", "reason": "connection_interrupted", "type": type(exc).__name__}))
        return 1
    finally:
        client.disconnect()
        client.loop_stop()
    print(json.dumps({
        "status": "ok" if adapter.receiver.accepted else "unknown",
        "accepted": adapter.receiver.accepted,
        "rejected": adapter.receiver.rejected,
        "observed_devices": len(adapter.receiver.freshness.last_seen),
        "reason": "valid_osd_received" if adapter.receiver.accepted else "no_valid_osd_received",
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
