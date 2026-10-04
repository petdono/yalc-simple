import unittest
from unittest.mock import patch

import config
from rate_limits import rate_limit_for, rate_limited


class RateLimitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.original_default = config.TUYA_MAX_UPDATES_PER_SECOND
        self.original_limits = dict(config.LIGHT_RATE_LIMITS)

    def tearDown(self) -> None:
        config.TUYA_MAX_UPDATES_PER_SECOND = self.original_default
        config.LIGHT_RATE_LIMITS = self.original_limits

    def test_blank_tuya_uses_default_but_other_providers_are_unlimited(self) -> None:
        config.TUYA_MAX_UPDATES_PER_SECOND = 6
        config.LIGHT_RATE_LIMITS = {}

        self.assertEqual(rate_limit_for("Tuya", "device-id"), 6)
        self.assertIsNone(rate_limit_for("LIFX", "device-id"))
        self.assertIsNone(rate_limit_for("Govee", "device-id"))

    def test_explicit_per_light_limit_overrides_provider_default(self) -> None:
        config.TUYA_MAX_UPDATES_PER_SECOND = 6
        config.LIGHT_RATE_LIMITS = {
            "tuya:device-id": 3,
            "lifx:device-id": 4,
            "govee:device-id": 5,
        }

        self.assertEqual(rate_limit_for("Tuya", "DEVICE-ID"), 3)
        self.assertEqual(rate_limit_for("LIFX", "device-id"), 4)
        self.assertEqual(rate_limit_for("Govee", "device-id"), 5)

    def test_explicit_limit_spaces_commands_for_each_light(self) -> None:
        config.LIGHT_RATE_LIMITS = {"tuya:rate-limit-test": 4}

        with (
            patch("rate_limits.time.monotonic", side_effect=[10, 10, 10.1, 10.25]),
            patch("rate_limits.time.sleep") as sleep,
        ):
            with rate_limited("tuya", "rate-limit-test"):
                pass
            with rate_limited("tuya", "rate-limit-test"):
                pass

        sleep.assert_called_once_with(0.15000000000000036)


if __name__ == "__main__":
    unittest.main()
