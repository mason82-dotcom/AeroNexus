"""MQTT callback adapter for read-only DJI OSD observation.

An externally configured MQTT client must authenticate as the ACL-limited
'companion' user. This module never connects, publishes, or changes EMQX.
"""
from passive_osd_receiver import PassiveOsdReceiver

OSD_SUBSCRIPTION = "thing/product/+/osd"


class ReadOnlyOsdMqttAdapter:
    def __init__(self, receiver: PassiveOsdReceiver | None = None):
        self.receiver = receiver if receiver is not None else PassiveOsdReceiver()
        self.subscribed = False

    def on_connect(self, client, userdata, flags, reason_code, properties=None):
        """Subscribe only after a successful MQTT connection."""
        self.subscribed = False
        if reason_code != 0:
            return
        result = client.subscribe(OSD_SUBSCRIPTION, qos=0)
        # Paho returns (result_code, mid); success is result_code == 0.
        self.subscribed = isinstance(result, tuple) and len(result) >= 1 and result[0] == 0

    def on_disconnect(self, client, userdata, *args):
        self.subscribed = False

    def on_message(self, client, userdata, message):
        if not self.subscribed:
            return
        self.receiver.accept(message.topic, message.payload)

    def status(self, device_id: str, device_connected: bool | None):
        """A broken transport cannot be reported as active telemetry."""
        return self.receiver.status(device_id, device_connected if self.subscribed else None)
