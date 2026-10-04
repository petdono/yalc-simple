import unittest
from unittest.mock import patch

import config
from lifx import LifxController
from yarg import LightingIntent


class FakeLight:
    def __init__(self, mac: str, ip: str) -> None:
        self.mac = mac
        self.ip = ip
        self.commands: list[tuple[object, ...]] = []

    def get_mac_addr(self) -> str:
        return self.mac

    def get_label(self) -> str:
        return "Discovered label"

    def get_ip_addr(self) -> str:
        return self.ip

    def set_color(
        self, color: tuple[int, int, int, int], duration: int, rapid: bool
    ) -> None:
        self.commands.append(("color", color, duration, rapid))

    def set_power(self, power: bool, rapid: bool) -> None:
        self.commands.append(("power", power, rapid))


class LifxControllerTests(unittest.TestCase):
    def test_rate_test_sends_selected_color_to_one_light(self) -> None:
        light = FakeLight("aa:bb:cc:dd:ee:ff", "192.168.1.10")
        controller = LifxController()
        controller._all_lights = [light]
        controller._all_labels = {light.mac: "Desk lamp"}
        controller._all_ips = {light.mac: light.ip}
        with patch.object(config, "BRIGHTNESS_MULTIPLIER", 0.5):
            controller.test_light_color(light.ip, "RED")

        self.assertEqual(
            light.commands,
            [
                ("color", (65535, 65535, 32768, 3500), 0, True),
                ("power", True, True),
            ],
        )
        controller.close()

    def test_start_scans_once_and_commands_do_not_trigger_discovery(self) -> None:
        controller = LifxController()
        with patch.object(controller, "_discover") as discover:
            controller.start()
            controller.submit(LightingIntent("RED"))
            self.assertTrue(controller.wait_until_applied(1))
            controller.close()
        self.assertEqual(discover.call_count, 1)

    def test_manual_light_bypasses_allow_list_and_respects_exclusions(self) -> None:
        manual_light = ("lifx", "Manual desk", "192.168.1.70", "aa:bb:cc:dd:ee:ff")
        with (
            patch.object(config, "INCLUDE_LIGHTS", ("some other lamp",)),
            patch.object(config, "EXCLUDE_LIGHTS", ()),
            patch.object(config, "MANUAL_LIGHTS", (manual_light,)),
            patch("lifx.Light", side_effect=lambda mac, ip, **_: FakeLight(mac, ip)),
        ):
            controller = LifxController()
            with patch.object(controller._lan, "get_lights", return_value=[]):
                controller._discover()
            self.assertEqual(
                controller.discovered_devices, [("Manual desk", "192.168.1.70")]
            )
            controller.close()

        with (
            patch.object(config, "INCLUDE_LIGHTS", ()),
            patch.object(config, "EXCLUDE_LIGHTS", ("192.168.1.70",)),
            patch.object(config, "MANUAL_LIGHTS", (manual_light,)),
            patch("lifx.Light", side_effect=lambda mac, ip, **_: FakeLight(mac, ip)),
        ):
            controller = LifxController()
            with patch.object(controller._lan, "get_lights", return_value=[]):
                controller._discover()
            self.assertEqual(controller.discovered_devices, [])
            self.assertEqual(
                controller.all_discovered_devices, [("Manual desk", "192.168.1.70")]
            )
            with patch.object(config, "EXCLUDE_LIGHTS", ()):
                controller.apply_filters()
            self.assertEqual(
                controller.discovered_devices, [("Manual desk", "192.168.1.70")]
            )
            controller.close()


if __name__ == "__main__":
    unittest.main()
