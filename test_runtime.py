import queue
import socket
import struct
import threading
import unittest
from unittest.mock import patch

import config
from runtime import LightingOutputs, YargBridge
from yarg import LightingIntent


class FakeController:
    def __init__(self) -> None:
        self.intents: queue.Queue[LightingIntent] = queue.Queue()
        self.closed = threading.Event()

    def start(self) -> None:
        pass

    def submit(self, intent: LightingIntent) -> None:
        self.intents.put(intent)

    def close(self) -> None:
        self.closed.set()

    @property
    def discovered_devices(self) -> list[tuple[str, str]]:
        return [("Mock", "192.0.2.1")]


def make_packet() -> bytes:
    data = bytearray(47)
    struct.pack_into("<IB", data, 0, 0x59415247, 3)
    data[13] = 5
    data[34] = 5
    data[37] = 24
    return bytes(data)


class BridgeTests(unittest.TestCase):
    def test_malformed_packet_does_not_stop_udp_listener(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        controller = FakeController()
        with (
            patch.object(config, "YARG_UDP_PORT", port),
            patch("runtime.LightingOutputs", return_value=controller),
        ):
            bridge = YargBridge()
            bridge.start()
            try:
                self.assertTrue(bridge.ready.wait(3))
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender:
                    sender.sendto(b"bad packet", ("127.0.0.1", port))
                    sender.sendto(make_packet(), ("127.0.0.1", port))
                intent = controller.intents.get(timeout=3)
                self.assertEqual(intent, LightingIntent("BLUE", transition_ms=120))
            finally:
                bridge.stop()

        self.assertTrue(controller.closed.wait(1))
        self.assertFalse(bridge.running)

    def test_shared_lighting_intent_is_sent_to_both_backends(self) -> None:
        lifx = FakeController()
        govee = FakeController()
        with (
            patch.object(config, "GOVEE_ENABLED", True),
            patch("runtime.LifxController", return_value=lifx),
            patch("runtime.GoveeController", return_value=govee),
        ):
            outputs = LightingOutputs()
            outputs.start()
            intent = LightingIntent("PURPLE", transition_ms=300)
            outputs.submit(intent)
            self.assertEqual(lifx.intents.get(timeout=1), intent)
            self.assertEqual(govee.intents.get(timeout=1), intent)
            outputs.close()

        self.assertTrue(lifx.closed.is_set())
        self.assertTrue(govee.closed.is_set())


if __name__ == "__main__":
    unittest.main()
