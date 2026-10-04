import queue
import socket
import struct
import threading
import unittest
from unittest.mock import Mock, patch

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
            patch.object(config, "TUYA_ENABLED", False),
            patch("runtime.LightingOutputs", return_value=controller),
        ):
            bridge = YargBridge()
            with self.assertLogs(level="INFO") as captured:
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

        self.assertTrue(
            any("Received first valid YARG UDP datagram" in line for line in captured.output)
        )
        self.assertTrue(controller.closed.wait(1))
        self.assertFalse(bridge.running)

    def test_shared_lighting_intent_is_sent_to_both_backends(self) -> None:
        lifx = FakeController()
        govee = FakeController()
        with (
            patch.object(config, "GOVEE_ENABLED", True),
            patch.object(config, "TUYA_ENABLED", False),
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

    def test_shared_lighting_intent_is_sent_to_tuya_backend(self) -> None:
        tuya = FakeController()
        with (
            patch.object(config, "LIFX_ENABLED", False),
            patch.object(config, "GOVEE_ENABLED", False),
            patch.object(config, "TUYA_ENABLED", True),
            patch("runtime.TuyaController", return_value=tuya),
        ):
            outputs = LightingOutputs()
            outputs.start()
            intent = LightingIntent("CYAN", transition_ms=150)
            outputs.submit(intent)
            self.assertEqual(tuya.intents.get(timeout=1), intent)
            outputs.close()
        self.assertTrue(tuya.closed.is_set())

    def test_rate_test_routes_color_to_selected_backend_and_device(self) -> None:
        lifx = FakeController()
        with (
            patch.object(config, "GOVEE_ENABLED", False),
            patch.object(config, "TUYA_ENABLED", False),
            patch("runtime.LifxController", return_value=lifx),
        ):
            outputs = LightingOutputs()
            outputs.start()
            lifx.test_light_color = Mock()
            outputs.test_light_color("LIFX", "192.0.2.1", "PURPLE")
            lifx.test_light_color.assert_called_once_with("192.0.2.1", "PURPLE")
            outputs.close()

    def test_no_enabled_provider_does_not_report_a_command_as_applied(self) -> None:
        with (
            patch.object(config, "LIFX_ENABLED", False),
            patch.object(config, "GOVEE_ENABLED", False),
            patch.object(config, "TUYA_ENABLED", False),
        ):
            outputs = LightingOutputs()
            outputs.start()
            outputs.submit(LightingIntent("BLUE"))
            self.assertFalse(outputs.wait_until_applied(0.01))
            outputs.close()

    def test_bridge_reuses_gui_outputs_without_closing_them_on_stop(self) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]

        with (
            patch.object(config, "YARG_UDP_PORT", port),
            patch.object(config, "LIFX_ENABLED", False),
            patch.object(config, "GOVEE_ENABLED", False),
            patch.object(config, "TUYA_ENABLED", False),
        ):
            output = LightingOutputs()
            output.close = Mock()
            bridge = YargBridge(output)
            bridge.start()
            try:
                self.assertTrue(bridge.ready.wait(2))
            finally:
                bridge.stop()
        output.close.assert_not_called()

    def test_manual_scan_calls_scan_without_restarting_backends(self) -> None:
        lifx = FakeController()
        govee = FakeController()
        with (
            patch.object(config, "GOVEE_ENABLED", True),
            patch.object(config, "TUYA_ENABLED", False),
            patch("runtime.LifxController", return_value=lifx),
            patch("runtime.GoveeController", return_value=govee),
        ):
            outputs = LightingOutputs()
            outputs.start()
            outputs.lifx.scan = Mock()
            outputs.govee.scan = Mock()
            outputs.scan()
            outputs.lifx.scan.assert_called_once()
            outputs.govee.scan.assert_called_once()
            outputs.close()


if __name__ == "__main__":
    unittest.main()
