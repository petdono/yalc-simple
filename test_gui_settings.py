import unittest
from unittest.mock import Mock, patch

import config
from gui import YargLifxWindow, _apply_settings, _light_exclusions


class GuiSettingsTests(unittest.TestCase):
    def test_tuya_rate_save_persists_valid_limit(self) -> None:
        class Value:
            def __init__(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        original_rate = config.TUYA_MAX_UPDATES_PER_SECOND
        window = YargLifxWindow.__new__(YargLifxWindow)
        window.tuya_rate = Value("7")
        window._persist_settings = Mock()
        try:
            self.assertTrue(window._save_tuya_rate(None))
            self.assertEqual(config.TUYA_MAX_UPDATES_PER_SECOND, 7)
            self.assertEqual(window.tuya_rate.get(), "7")
            window._persist_settings.assert_called_once()
        finally:
            config.TUYA_MAX_UPDATES_PER_SECOND = original_rate

    def test_tuya_rate_save_rejects_out_of_range_values(self) -> None:
        class Value:
            def get(self) -> str:
                return "51"

            def set(self) -> None:
                raise AssertionError("invalid rate should not be normalized")

        window = YargLifxWindow.__new__(YargLifxWindow)
        window.tuya_rate = Value()
        window._persist_settings = Mock()
        with patch("gui.messagebox.showerror") as show_error:
            self.assertFalse(window._save_tuya_rate(None))
        window._persist_settings.assert_not_called()
        show_error.assert_called_once()

    def test_per_light_rate_saves_and_blank_clears_override(self) -> None:
        class Value:
            def __init__(self, value: str) -> None:
                self.value = value

            def get(self) -> str:
                return self.value

            def set(self, value: str) -> None:
                self.value = value

        original = dict(config.LIGHT_RATE_LIMITS)
        window = YargLifxWindow.__new__(YargLifxWindow)
        window._persist_settings = Mock()
        rate = Value("4")
        try:
            self.assertTrue(
                window._save_light_rate_limit("tuya:device-id", "Tuya", rate, None)
            )
            self.assertEqual(config.LIGHT_RATE_LIMITS["tuya:device-id"], 4)
            self.assertTrue(
                window._save_light_rate_limit("tuya:device-id", "Tuya", Value(""), None)
            )
            self.assertNotIn("tuya:device-id", config.LIGHT_RATE_LIMITS)
            self.assertEqual(window._persist_settings.call_count, 2)
        finally:
            config.LIGHT_RATE_LIMITS = original

    def test_per_light_rate_rejects_invalid_rate_without_saving(self) -> None:
        class Value:
            def get(self) -> str:
                return "0"

            def set(self, value: str) -> None:
                raise AssertionError(f"invalid rate should not be normalized: {value}")

        window = YargLifxWindow.__new__(YargLifxWindow)
        window._persist_settings = Mock()
        with patch("gui.messagebox.showerror") as show_error:
            self.assertFalse(
                window._save_light_rate_limit(
                    "lifx:aa:bb:cc:dd:ee:ff", "LIFX", Value(), None
                )
            )
        window._persist_settings.assert_not_called()
        show_error.assert_called_once()

    def test_per_light_rate_rolls_back_when_persistence_fails(self) -> None:
        class Value:
            def get(self) -> str:
                return "4"

            def set(self, value: str) -> None:
                raise AssertionError(
                    f"failed persistence should not update the field: {value}"
                )

        original = dict(config.LIGHT_RATE_LIMITS)
        config.LIGHT_RATE_LIMITS = {"tuya:device-id": 8}
        window = YargLifxWindow.__new__(YargLifxWindow)
        window._persist_settings = Mock(side_effect=OSError("disk is full"))
        try:
            with patch("gui.messagebox.showerror") as show_error:
                self.assertFalse(
                    window._save_light_rate_limit(
                        "tuya:device-id", "Tuya", Value(), None
                    )
                )
            self.assertEqual(config.LIGHT_RATE_LIMITS, {"tuya:device-id": 8})
            show_error.assert_called_once()
        finally:
            config.LIGHT_RATE_LIMITS = original

    def test_light_checkbox_excludes_and_reincludes_case_insensitively(self) -> None:
        current = {"192.168.1.10", "192.168.1.11"}
        self.assertEqual(
            _light_exclusions(current, "192.168.1.10", False),
            {"192.168.1.10", "192.168.1.11"},
        )
        self.assertEqual(
            _light_exclusions(current, "192.168.1.10", True),
            {"192.168.1.11"},
        )
        self.assertEqual(
            _light_exclusions({"192.168.1.10"}, "192.168.1.10", True),
            set(),
        )

    def test_settings_are_validated_before_being_applied(self) -> None:
        original = {
            "YARG_UDP_PORT": config.YARG_UDP_PORT,
            "LIFX_DISCOVERY_TIMEOUT": config.LIFX_DISCOVERY_TIMEOUT,
            "BRIGHTNESS_MULTIPLIER": config.BRIGHTNESS_MULTIPLIER,
            "LIFX_ENABLED": config.LIFX_ENABLED,
            "GOVEE_ENABLED": config.GOVEE_ENABLED,
            "TUYA_ENABLED": config.TUYA_ENABLED,
            "TUYA_MAX_UPDATES_PER_SECOND": config.TUYA_MAX_UPDATES_PER_SECOND,
            "LIGHT_RATE_LIMITS": config.LIGHT_RATE_LIMITS,
            "GOVEE_DISCOVERY_TIMEOUT": config.GOVEE_DISCOVERY_TIMEOUT,
            "GOVEE_INCLUDE_DEVICES": config.GOVEE_INCLUDE_DEVICES,
            "COLOR_TRANSITION_MS": config.COLOR_TRANSITION_MS,
            "INCLUDE_LIGHTS": config.INCLUDE_LIGHTS,
            "EXCLUDE_LIGHTS": config.EXCLUDE_LIGHTS,
            "MANUAL_LIGHTS": config.MANUAL_LIGHTS,
            "DEBUG_LOGGING": config.DEBUG_LOGGING,
        }
        try:
            with self.assertRaisesRegex(ValueError, "port"):
                _apply_settings({"yarg_udp_port": 70000})
            self.assertEqual(config.YARG_UDP_PORT, original["YARG_UDP_PORT"])

            _apply_settings(
                {
                    "yarg_udp_port": 36108,
                    "brightness_multiplier": 0.55,
                    "govee_enabled": False,
                    "tuya_enabled": False,
                    "tuya_max_updates_per_second": 4,
                    "light_rate_limits": {
                        "tuya:device-id": 3,
                        "lifx:aa:bb:cc:dd:ee:ff": 2,
                    },
                    "govee_discovery_timeout": 3.5,
                    "govee_include_devices": ["192.168.1.42", "H61xx"],
                    "include_lights": ["Hall lamp"],
                    "exclude_lights": ["192.168.1.55"],
                    "manual_lights": [
                        {
                            "provider": "lifx",
                            "name": "Desk lamp",
                            "ip": "192.168.1.56",
                            "mac": "aa:bb:cc:dd:ee:ff",
                        },
                        {
                            "provider": "govee",
                            "name": "Shelf strip",
                            "ip": "192.168.1.57",
                            "mac": "",
                        },
                    ],
                    "lifx_enabled": False,
                    "debug_logging": False,
                }
            )
            self.assertEqual(config.YARG_UDP_PORT, 36108)
            self.assertEqual(config.BRIGHTNESS_MULTIPLIER, 0.55)
            self.assertFalse(config.GOVEE_ENABLED)
            self.assertFalse(config.TUYA_ENABLED)
            self.assertEqual(config.TUYA_MAX_UPDATES_PER_SECOND, 4)
            self.assertEqual(
                config.LIGHT_RATE_LIMITS,
                {
                    "tuya:device-id": 3,
                    "lifx:aa:bb:cc:dd:ee:ff": 2,
                },
            )
            self.assertEqual(config.GOVEE_DISCOVERY_TIMEOUT, 3.5)
            self.assertEqual(
                config.GOVEE_INCLUDE_DEVICES, ("192.168.1.42", "H61xx")
            )
            self.assertEqual(config.INCLUDE_LIGHTS, ("Hall lamp",))
            self.assertEqual(config.EXCLUDE_LIGHTS, ("192.168.1.55",))
            self.assertEqual(
                config.MANUAL_LIGHTS,
                (
                    ("lifx", "Desk lamp", "192.168.1.56", "aa:bb:cc:dd:ee:ff"),
                    ("govee", "Shelf strip", "192.168.1.57", ""),
                ),
            )
            self.assertFalse(config.LIFX_ENABLED)
            self.assertFalse(config.DEBUG_LOGGING)
            with self.assertRaisesRegex(ValueError, "true or false"):
                _apply_settings({"debug_logging": "false"})
            with self.assertRaisesRegex(ValueError, "MAC address"):
                _apply_settings(
                    {
                        "manual_lights": [
                            {
                                "provider": "lifx",
                                "name": "Desk lamp",
                                "ip": "192.168.1.56",
                                "mac": "",
                            }
                        ]
                    }
                )
        finally:
            for name, value in original.items():
                setattr(config, name, value)


if __name__ == "__main__":
    unittest.main()
