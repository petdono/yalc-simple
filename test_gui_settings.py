import unittest

import config
from gui import _apply_settings


class GuiSettingsTests(unittest.TestCase):
    def test_settings_are_validated_before_being_applied(self) -> None:
        original = {
            "YARG_UDP_PORT": config.YARG_UDP_PORT,
            "LIFX_DISCOVERY_TIMEOUT": config.LIFX_DISCOVERY_TIMEOUT,
            "LIFX_REDISCOVERY_INTERVAL": config.LIFX_REDISCOVERY_INTERVAL,
            "BRIGHTNESS_MULTIPLIER": config.BRIGHTNESS_MULTIPLIER,
            "GOVEE_ENABLED": config.GOVEE_ENABLED,
            "GOVEE_DISCOVERY_TIMEOUT": config.GOVEE_DISCOVERY_TIMEOUT,
            "GOVEE_INCLUDE_DEVICES": config.GOVEE_INCLUDE_DEVICES,
            "COLOR_TRANSITION_MS": config.COLOR_TRANSITION_MS,
            "INCLUDE_LIGHTS": config.INCLUDE_LIGHTS,
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
                    "govee_discovery_timeout": 3.5,
                    "govee_include_devices": ["192.168.1.42", "H61xx"],
                    "include_lights": ["Hall lamp"],
                    "debug_logging": False,
                }
            )
            self.assertEqual(config.YARG_UDP_PORT, 36108)
            self.assertEqual(config.BRIGHTNESS_MULTIPLIER, 0.55)
            self.assertFalse(config.GOVEE_ENABLED)
            self.assertEqual(config.GOVEE_DISCOVERY_TIMEOUT, 3.5)
            self.assertEqual(
                config.GOVEE_INCLUDE_DEVICES, ("192.168.1.42", "H61xx")
            )
            self.assertEqual(config.INCLUDE_LIGHTS, ("Hall lamp",))
            self.assertFalse(config.DEBUG_LOGGING)
            with self.assertRaisesRegex(ValueError, "true or false"):
                _apply_settings({"debug_logging": "false"})
        finally:
            for name, value in original.items():
                setattr(config, name, value)


if __name__ == "__main__":
    unittest.main()
