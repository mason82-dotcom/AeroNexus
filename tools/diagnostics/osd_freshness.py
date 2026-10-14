"""Pure, offline-testable OSD freshness state machine.

No MQTT connection, credentials, filesystem writes or Docker operations.
Timestamps represent local monotonic receipt time, never device clock time.
"""
from dataclasses import dataclass, field
from math import isfinite


@dataclass
class OsdFreshness:
    stale_after_seconds: float = 10.0
    last_seen: dict[str, float] = field(default_factory=dict)

    def __post_init__(self):
        if not isfinite(self.stale_after_seconds) or self.stale_after_seconds <= 0:
            raise ValueError("stale_after_seconds must be finite and positive")

    def record(self, device_id: str, received_at: float) -> None:
        """Record receipt of a validated OSD message (validation by caller)."""
        if not device_id or not isinstance(device_id, str):
            raise ValueError("device_id must be nonempty")
        if not isfinite(received_at):
            raise ValueError("received_at must be finite")
        previous = self.last_seen.get(device_id)
        if previous is None or received_at > previous:
            self.last_seen[device_id] = received_at

    def status(self, device_id: str, now: float, connected: bool | None) -> dict:
        if not isfinite(now):
            raise ValueError("now must be finite")
        if connected is False:
            return {"status": "ok", "telemetry": "inactive", "reason": "device_offline"}
        if connected is None:
            return {"status": "unknown", "telemetry": "unverified",
                    "reason": "device_connection_unknown"}
        last = self.last_seen.get(device_id)
        if last is None or now < last:
            return {"status": "unknown", "telemetry": "unverified",
                    "reason": "no_valid_osd_received"}
        age = now - last
        if age > self.stale_after_seconds:
            return {"status": "warning", "telemetry": "stale",
                    "age_seconds": round(age, 2)}
        return {"status": "ok", "telemetry": "active",
                "age_seconds": round(age, 2)}
