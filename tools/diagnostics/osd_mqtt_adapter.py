"""MQTT callback adapter for the dedicated, ACL-restricted OSD observer.

This module never connects, publishes, or changes EMQX. A successful
SUBSCRIBE request is not proof that the broker granted the subscription.
"""
from passive_osd_receiver import PassiveOsdReceiver

OSD_SUBSCRIPTION = "thing/product/+/osd"


def _code(reason_code):
    """paho-mqtt 2.x passes ReasonCode objects (compare with ==, but no int());
    1.x and the unit tests pass plain integers."""
    return int(getattr(reason_code, "value", reason_code))


class ReadOnlyOsdMqttAdapter:
    def __init__(self, receiver: PassiveOsdReceiver | None = None):
        self.receiver = receiver if receiver is not None else PassiveOsdReceiver()
        self.subscribed = False
        self._pending_mid = None

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        """Request OSD only after authentication; await broker SUBACK."""
        self.subscribed = False
        self._pending_mid = None
        if reason_code != 0:
            return
        result = client.subscribe(OSD_SUBSCRIPTION, qos=0)
        if isinstance(result, tuple) and len(result) >= 2 and result[0] == 0:
            self._pending_mid = result[1]

    def on_subscribe(self, client, userdata, mid, reason_codes, properties=None):
        """Mark subscribed only when SUBACK grants the requested QoS."""
        if self._pending_mid is None or mid != self._pending_mid:
            return
        self._pending_mid = None
        self.subscribed = (
            len(reason_codes) == 1
            and _code(reason_codes[0]) in (0, 1, 2)
        )

    def on_disconnect(self, client, userdata, *args):
        self.subscribed = False
        self._pending_mid = None

    def on_message(self, client, userdata, message):
        if not self.subscribed:
            return
        self.receiver.accept(message.topic, message.payload)

    def status(self, device_id: str, device_connected: bool | None):
        """A broken transport cannot be reported as active telemetry."""
        return self.receiver.status(device_id, device_connected if self.subscribed else None)
