import json
import socket
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import config
import tuya
from tuya import TuyaController, TuyaLight, advertised_capabilities
from yarg import LightingIntent


class FakeBulb:
    def __init__(self, *args: object, **kwargs: object) -> None:
        self.args = args
        self.kwargs = kwargs
        self.dpset = {
            "switch": 1,
            "mode": 2,
            "brightness": 3,
            "colourtemp": 4,
            "colour": 5,
        }
        self.bulb_configured = True
        self.calls: list[tuple[object, ...]] = []

    def set_socketRetryLimit(self, limit: int) -> None:
        self.calls.append(("retry", limit))

    def status(self) -> dict[str, object]:
        return {"dps": {"1": False, "2": "white", "3": 100, "4": 0, "5": "000000000000"}}

    def set_colour(self, red: int, green: int, blue: int, nowait: bool = False) -> None:
        self.calls.append(("colour", red, green, blue, nowait))

    def set_white_percentage(
        self, brightness: int, colourtemp: int, nowait: bool = False
    ) -> None:
        self.calls.append(("white", brightness, colourtemp, nowait))

    def turn_on(self, switch: int = 0, nowait: bool = False) -> None:
        self.calls.append(("on", switch, nowait))

    def turn_off(self, switch: int = 0, nowait: bool = False) -> None:
        self.calls.append(("off", switch, nowait))

    def close(self) -> None:
        self.calls.append(("close",))


def saved_light() -> dict[str, object]:
    return {
        "id": "device-id",
        "name": "Bedroom bulb",
        "local_key": "sensitive-local-key",
        "category": "dj",
        "product_id": "product-id",
        "product_name": "RGB bulb",
        "uuid": "uuid",
        "capabilities": {
            "power": 1,
            "mode": 2,
            "brightness": 3,
            "colourtemp": 4,
            "colour": 5,
        },
        "protocol_version": None,
        "ip": None,
    }


class TuyaCapabilityTests(unittest.TestCase):
    def test_state_directory_uses_platform_standard_config_location(self) -> None:
        home = Path.home()
        self.assertEqual(
            tuya._state_directory(
                "linux",
                {"XDG_CONFIG_HOME": str(home / "xdg")},
                home,
            ),
            home / "xdg" / "YARG-LIFX",
        )
        self.assertEqual(
            tuya._state_directory(
                "linux",
                {"XDG_CONFIG_HOME": "relative-config"},
                home,
            ),
            home / ".config" / "YARG-LIFX",
        )
        self.assertEqual(
            tuya._state_directory(
                "win32",
                {"APPDATA": str(home / "Roaming")},
                home,
            ),
            home / "Roaming" / "YARG-LIFX",
        )

    def test_category_and_advertised_local_dp_mapping_are_required(self) -> None:
        functions = {
            "switch_led": {},
            "work_mode": {},
            "bright_value": {},
            "colour_data": {},
        }
        strategy = {
            1: {"status_code": "switch_led"},
            2: {"status_code": "work_mode"},
            3: {"status_code": "bright_value"},
            5: {"status_code": "colour_data"},
        }
        self.assertEqual(
            advertised_capabilities("dj", functions, strategy),
            {"power": 1, "mode": 2, "brightness": 3, "colour": 5},
        )
        self.assertIsNone(advertised_capabilities("cz", functions, strategy))
        self.assertIsNone(
            advertised_capabilities(
                "dj", {"colour_data": {}}, {5: {"status_code": "colour_data"}}
            )
        )

    def test_lan_profile_is_narrowed_to_cloud_advertised_datapoints(self) -> None:
        client = FakeBulb()
        record = saved_light()
        del record["capabilities"]["colourtemp"]
        verified = tuya._matches_advertised_datapoints(
            record,
            client,
            {"dps": {"1": False, "2": "white", "3": 100, "4": 0, "5": "000000000000"}},
        )
        self.assertEqual(verified, {"power": 1, "mode": 2, "brightness": 3, "colour": 5})
        self.assertIsNone(client.dpset["colourtemp"])


class TuyaLoginTests(unittest.TestCase):
    def test_cached_state_filters_unknown_categories_and_malformed_datapoints(self) -> None:
        valid = saved_light()
        non_light = dict(valid, id="plug", category="cz")
        invalid = dict(valid, id="invalid", capabilities={"power": True})
        with tempfile.TemporaryDirectory() as directory:
            state_directory = Path(directory)
            state_file = state_directory / "tuya_devices.json"
            with (
                patch.object(tuya, "STATE_DIRECTORY", state_directory),
                patch.object(tuya, "STATE_FILE", state_file),
            ):
                tuya._save_records([valid, non_light, invalid])
                loaded = tuya.load_credentials()

        self.assertEqual([record["id"] for record in loaded], ["device-id"])
        self.assertEqual(loaded[0]["capabilities"]["power"], 1)

    def test_qr_authorization_saves_only_locally_controllable_lights(self) -> None:
        device = types.SimpleNamespace(
            id="device-id",
            name="Bedroom bulb",
            local_key="sensitive-local-key",
            category="dj",
            product_id="product-id",
            product_name="RGB bulb",
            uuid="uuid",
            support_local=True,
            function={
                "switch_led": object(),
                "work_mode": object(),
                "bright_value": object(),
                "colour_data": object(),
            },
            local_strategy={
                1: {"status_code": "switch_led"},
                2: {"status_code": "work_mode"},
                3: {"status_code": "bright_value"},
                5: {"status_code": "colour_data"},
            },
        )

        class LoginControl:
            def qr_code(self, client_id: str, schema: str, user_code: str) -> dict[str, object]:
                self.qr_args = client_id, schema, user_code
                return {"success": True, "result": {"qrcode": "short-lived-token"}}

            def login_result(
                self, token: str, client_id: str, user_code: str
            ) -> tuple[bool, dict[str, str]]:
                self.login_args = token, client_id, user_code
                return True, {
                    "terminal_id": "terminal-id",
                    "endpoint": "https://tuya.example",
                    "access_token": "temporary-session-token",
                }

        class Manager:
            def __init__(self, *_: object) -> None:
                self.device_map = {"device-id": device}

            def update_device_cache(self) -> None:
                pass

        fake_sdk = types.ModuleType("tuya_sharing")
        fake_sdk.LoginControl = LoginControl
        fake_sdk.Manager = Manager
        qr_payloads: list[str] = []
        statuses: list[str] = []
        with tempfile.TemporaryDirectory() as directory:
            state_directory = Path(directory)
            with (
                patch.dict(sys.modules, {"tuya_sharing": fake_sdk}),
                patch.object(tuya, "STATE_DIRECTORY", state_directory),
                patch.object(tuya, "STATE_FILE", state_directory / "tuya_devices.json"),
                patch.object(tuya.time, "sleep"),
            ):
                names = tuya.connect_account(
                    "user-code", qr_payloads.append, statuses.append
                )
                saved = json.loads(tuya.STATE_FILE.read_text(encoding="utf-8"))

        self.assertEqual(names, ["Bedroom bulb"])
        self.assertEqual(
            qr_payloads, ["smartlife--qrLogin?token=short-lived-token"]
        )
        self.assertIn("Approved. Retrieving local device information...", statuses)
        self.assertEqual(saved["devices"][0]["capabilities"]["colour"], 5)
        self.assertEqual(saved["devices"][0]["local_key"], "sensitive-local-key")
        self.assertNotIn("temporary-session-token", json.dumps(saved))

    def test_qr_matrix_is_square_and_renderable(self) -> None:
        matrix = tuya.qr_matrix("smartlife--qrLogin?token=test")
        self.assertTrue(matrix)
        self.assertEqual(len(matrix), len(matrix[0]))
        self.assertTrue(all(isinstance(cell, bool) for row in matrix for cell in row))

    def test_logout_removes_only_tuya_device_cache(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_directory = Path(directory)
            state_file = state_directory / "tuya_devices.json"
            settings_file = state_directory / "settings.json"
            state_file.write_text("{}", encoding="utf-8")
            settings_file.write_text("{}", encoding="utf-8")
            with patch.object(tuya, "STATE_FILE", state_file):
                self.assertTrue(tuya.logout())
                self.assertFalse(tuya.logout())
            self.assertFalse(state_file.exists())
            self.assertTrue(settings_file.exists())


class TuyaControllerTests(unittest.TestCase):
    def test_scanner_compatibility_preserves_already_encoded_bytes(self) -> None:
        class FakeXenonDevice:
            def _encode_message(self, message: object) -> bytes:
                payload = getattr(message, "payload")
                return b"encoded:" + payload

        module = types.ModuleType("tinytuya.core.XenonDevice")
        module.XenonDevice = FakeXenonDevice
        with patch.dict(sys.modules, {"tinytuya.core.XenonDevice": module}):
            tuya._make_scanner_message_encoding_idempotent()
            client = FakeXenonDevice()
            raw_message = b"already-encoded"
            self.assertIs(client._encode_message(raw_message), raw_message)
            message = types.SimpleNamespace(payload=b"payload")
            self.assertEqual(client._encode_message(message), b"encoded:payload")

    def test_lan_scan_supplies_cached_credentials_to_tinytuya_ip_scan(self) -> None:
        devices = [
            {
                "id": "device-id",
                "key": "sensitive-local-key",
                "name": "Bedroom bulb",
            }
        ]
        scanner = types.SimpleNamespace(
            devices=lambda **kwargs: self._capture_scan_kwargs(kwargs)
        )
        tiny_module = types.ModuleType("tinytuya")
        tiny_module.scanner = scanner
        tiny_module.__path__ = []
        core_module = types.ModuleType("tinytuya.core")
        core_module.__path__ = []
        xenon_module = types.ModuleType("tinytuya.core.XenonDevice")

        class FakeXenonDevice:
            def _encode_message(self, message: object) -> object:
                return message

        xenon_module.XenonDevice = FakeXenonDevice
        self.scan_kwargs: dict[str, object] = {}
        route_socket = unittest.mock.MagicMock()
        route_socket.getsockname.return_value = ("192.168.1.7", 0)
        interface_address = types.SimpleNamespace(
            family=socket.AF_INET,
            address="192.168.1.7",
            netmask="255.255.255.0",
        )
        psutil_module = types.ModuleType("psutil")
        psutil_module.net_if_addrs = lambda: {"Wi-Fi": [interface_address]}
        with patch.dict(
            sys.modules,
            {
                "tinytuya": tiny_module,
                "tinytuya.core": core_module,
                "tinytuya.core.XenonDevice": xenon_module,
            },
        ):
            with (
                patch.dict(sys.modules, {"psutil": psutil_module}),
                patch("tuya.socket.socket", return_value=route_socket) as create_socket,
            ):
                found = tuya._scan_lan([saved_light()])

        self.assertEqual(found, {})
        create_socket.assert_called_once_with(socket.AF_INET, socket.SOCK_DGRAM)
        route_socket.connect.assert_called_once_with(("192.0.2.1", 9))
        route_socket.close.assert_called_once()
        self.assertEqual(self.scan_kwargs["tuyadevices"], devices)
        self.assertEqual(self.scan_kwargs["wantids"], ["device-id"])
        self.assertEqual(self.scan_kwargs["forcescan"], ["192.168.1.0/24"])
        self.assertFalse(self.scan_kwargs["poll"])
        self.assertFalse(self.scan_kwargs["discover"])
        self.assertTrue(self.scan_kwargs["assume_yes"])

    def _capture_scan_kwargs(self, kwargs: dict[str, object]) -> dict[str, object]:
        self.scan_kwargs = kwargs
        return {}

    def test_lan_scan_matches_cached_credentials_and_validates_tiny_profile(self) -> None:
        client = FakeBulb()
        tiny_module = types.ModuleType("tinytuya")
        tiny_module.BulbDevice = FakeBulb
        controller = TuyaController()
        with (
            patch("tuya.load_credentials", return_value=[saved_light()]),
            patch(
                "tuya._scan_lan",
                return_value={
                    "192.168.1.42": {"gwId": "device-id", "version": "3.3"}
                },
            ),
            patch.dict(sys.modules, {"tinytuya": tiny_module}),
            patch("tuya._save_records") as save_records,
        ):
            controller._discover()

        self.assertEqual(
            controller.discovered_devices, [("Bedroom bulb", "192.168.1.42")]
        )
        client = controller._lights[0].client
        self.assertEqual(client.kwargs["version"], 3.3)
        self.assertEqual(controller._lights[0].capabilities["colour"], 5)
        save_records.assert_called_once()
        controller.close()

    def test_commands_are_local_deduplicated_and_handle_blackout(self) -> None:
        client = FakeBulb()
        light = TuyaLight(
            "device-id",
            "Bedroom bulb",
            "192.168.1.42",
            "3.3",
            {"power": 1, "mode": 2, "brightness": 3, "colour": 5},
            client,
        )
        controller = TuyaController()
        controller._lights = [light]
        with patch.object(config, "BRIGHTNESS_MULTIPLIER", 0.8):
            controller._apply(LightingIntent("RED"), strobe_on=True)
            controller._apply(LightingIntent("RED"), strobe_on=True)
            controller._apply(
                LightingIntent("BLUE", blackout=True), strobe_on=True
            )

        self.assertEqual(
            [call for call in client.calls if call[0] != "retry"],
            [("colour", 204, 0, 0, True), ("off", 1, True)],
        )
        controller.close()

    def test_rate_test_applies_color_to_selected_tuya_light(self) -> None:
        client = FakeBulb()
        light = TuyaLight(
            "device-id",
            "Bedroom bulb",
            "192.168.1.42",
            "3.3",
            {"power": 1, "mode": 2, "brightness": 3, "colour": 5},
            client,
        )
        controller = TuyaController()
        controller._all_lights = [light]
        with patch.object(config, "BRIGHTNESS_MULTIPLIER", 0.8):
            controller.test_light_color(light.ip, "GREEN")
        self.assertEqual(
            [call for call in client.calls if call[0] == "colour"],
            [("colour", 0, 204, 0, True)],
        )
        controller.close()

    def test_identifying_tunable_white_restores_previous_temperature(self) -> None:
        client = FakeBulb()
        light = TuyaLight(
            "device-id",
            "Bedroom bulb",
            "192.168.1.42",
            "3.3",
            {"power": 1, "mode": 2, "brightness": 3, "colourtemp": 4},
            client,
        )
        controller = TuyaController()
        controller._all_lights = [light]
        controller._lights = [light]
        with (
            patch.object(config, "BRIGHTNESS_MULTIPLIER", 0.8),
            patch("tuya.time.sleep"),
        ):
            controller._apply(LightingIntent("BLUE"), strobe_on=True)
            controller.identify("192.168.1.42")

        self.assertEqual(
            [call for call in client.calls if call[0] == "white"],
            [("white", 80, 100, True), ("white", 100, 0, True), ("white", 80, 100, True)],
        )
        controller.close()


if __name__ == "__main__":
    unittest.main()
