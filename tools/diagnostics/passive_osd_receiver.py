"""Passive DJI OSD receiver core. No network or broker credentials required.

A transport adapter may subscribe to thing/product/+/osd and call accept().
This module never publishes MQTT messages or stores payloads.
"""
import json
import re
import time

from osd_freshness import OsdFreshness

TOPIC_PATTERN = re.compile(r"^thing/product/([^/]+)/osd$")
MAX_PAYLOAD_BYTES = 65536


class PassiveOsdReceiver:
    def __init__(self, stale_after_seconds=10.0, clock=None):
        self.freshness = OsdFreshness(stale_after_seconds)
        self.clock = clock if clock is not None else time.monotonic
        self.accepted = 0
        self.rejected = 0

    def accept(self, topic: str, payload: bytes) -> bool:
        """Accept only well-formed, bounded OSD JSON objects; discard content."""
        match = TOPIC_PATTERN.fullmatch(topic) if isinstance(topic, str) else None
        if not match or not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_PAYLOAD_BYTES:
            self.rejected += 1
            return False
        try:
            message = json.loads(payload.decode("utf-8"))
        except (UnicodeError, ValueError):
            self.rejected += 1
            return False
        if not isinstance(message, dict) or not isinstance(message.get("data"), dict):
            self.rejected += 1
            return False
        if not message["data"]:
            self.rejected += 1
            return False
        self.freshness.record(match.group(1), self.clock())
        self.accepted += 1
        return True

    def status(self, device_id: str, connected: bool | None) -> dict:
        return self.freshness.status(device_id, self.clock(), connected)
