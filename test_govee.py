import json
import socket
import time
import unittest
from unittest.mock import patch

import config
from govee import (
    CONTROL_PORT,
    DISCOVERY_PORT,
    LISTEN_PORT,
    MULTICAST_GROUP,
    GoveeController,
    GoveeDevice,
    brightness_message,
    color_message,
    color_temperature_message,
    parse_discovery_response,
    power_message,
    scan_message,
)
from yarg import LightingIntent


class FakeDiscoverySocket:
    def __init__(self, response: bytes) -> None:
        self.response = response
        self.sent: list[tuple[bytes, tuple[str, int]]] = []
        self.options: list[tuple[object, ...]] = []
        self.bound: tuple[str, int] | None = None
        self.timeout = 0.0
        self._responded = False
        self.closed = False

    def setsockopt(self, *args: object) -> None:
        self.options.append(args)

    def bind(self, address: tuple[str, int]) -> None:
        self.bound = address

    def sendto(self, packet: bytes, address: tuple[str, int]) -> None:
        self.sent.append((packet, address))

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def recvfrom(self, size: int) -> tuple[bytes, tuple[str, int]]:
        if not self._responded:
            self._responded = True
            return self.response[:size], ("192.168.1.35", 4002)
        time.sleep(min(0.005, self.timeout))
        raise socket.timeout

    def close(self) -> None:
        self.closed = True


class GoveeLanTests(unittest.TestCase):
    def test_protocol_payloads_match_lan_command_shapes(self) -> None:
        self.assertEqual(
            json.loads(scan_message()),
            {"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}},
        )
        self.assertEqual(
            json.loads(color_message((255, 0, 0))),
            {
                "msg": {
                    "cmd": "colorwc",
                    "data": {
                        "color": {"r": 255, "g": 0, "b": 0},
                        "colorTemInKelvin": 0,
                    },
                }
            },
        )
        self.assertEqual(
            json.loads(brightness_message(80))["msg"]["data"]["value"], 80
        )
        self.assertEqual(json.loads(power_message(False))["msg"]["data"]["value"], 0)
        self.assertEqual(
            json.loads(color_temperature_message(4000))["msg"]["data"][
                "colorTemInKelvin"
            ],
            4000,
        )
        with self.assertRaises(ValueError):
            color_temperature_message(1000)

    def test_scan_uses_documented_multicast_and_response_socket(self) -> None:
        response = json.dumps(
            {
                "msg": {
                    "cmd": "scan",
                    "data": {
                        "device": "AABBCC",
                        "sku": "H619D",
                        "deviceName": "Bedroom strip",
                    },
                }
            }
        ).encode()
        fake_socket = FakeDiscoverySocket(response)
        with (
            patch.object(config, "GOVEE_DISCOVERY_TIMEOUT", 0.03),
            patch.object(config, "GOVEE_INCLUDE_DEVICES", ()),
            patch("govee.socket.socket", return_value=fake_socket),
        ):
            controller = GoveeController()
            controller._discover()

        self.assertEqual(fake_socket.bound, ("", LISTEN_PORT))
        self.assertEqual(fake_socket.sent, [(scan_message(), (MULTICAST_GROUP, DISCOVERY_PORT))])
        self.assertEqual(
            controller.discovered_devices, [("Bedroom strip", "192.168.1.35")]
        )
        controller.close()

    def test_response_parser_ignores_other_udp_and_malformed_messages(self) -> None:
        self.assertIsNone(parse_discovery_response(b"not json", "192.0.2.1"))
        self.assertIsNone(
            parse_discovery_response(
                b'{"msg":{"cmd":"devStatus","data":{}}}', "192.0.2.1"
            )
        )
        device = parse_discovery_response(
            b'{"msg":{"cmd":"scan","data":{"device":"id","sku":"H6008"}}}',
            "192.0.2.2",
        )
        self.assertEqual(device, GoveeDevice("192.0.2.2", "id", "H6008", "H6008"))

    def test_state_cache_and_individual_device_failure(self) -> None:
        controller = GoveeController()
        first = GoveeDevice("192.168.1.10", "one", "H6008", "First")
        second = GoveeDevice("192.168.1.11", "two", "H6008", "Second")
        controller._devices = [first, second]
        sent: list[tuple[str, dict[str, object]]] = []

        def send(ip: str, payload: bytes) -> None:
            if ip == first.ip:
                raise OSError("unreachable")
            sent.append((ip, json.loads(payload)["msg"]))

        with patch.object(controller, "_send", side_effect=send):
            controller._apply(LightingIntent("RED"), strobe_on=True)
            controller._apply(LightingIntent("RED"), strobe_on=True)
            controller._apply(LightingIntent("RED", blackout=True), strobe_on=True)

        self.assertEqual(len(sent), 4)
        self.assertEqual(
            [command["cmd"] for _, command in sent],
            ["colorwc", "brightness", "turn", "turn"],
        )
        self.assertEqual(
            [command["data"]["color"] for _, command in sent if command["cmd"] == "colorwc"],
            [{"r": 255, "g": 0, "b": 0}],
        )
        self.assertEqual(CONTROL_PORT, 4003)
        controller.close()

    def test_start_scans_once_then_waits_for_rediscovery_interval(self) -> None:
        controller = GoveeController()
        with patch.object(controller, "_discover") as discover:
            controller.start()
            time.sleep(0.05)
            controller.close()
        self.assertEqual(discover.call_count, 1)


if __name__ == "__main__":
    unittest.main()
