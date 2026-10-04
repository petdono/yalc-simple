"""Smart Life account onboarding and local-only Tuya light control."""

from __future__ import annotations

import importlib
import ipaddress
import json
import logging
import os
import queue
import socket
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import config
from rate_limits import rate_limited
from yarg import LightingIntent

logger = logging.getLogger(__name__)

CLIENT_ID = "HA_3y9q4ak7g4ephrvke"
SCHEMA = "haauthorize"
QR_SCHEME = "smartlife"
STATE_DIRECTORY = Path(os.environ.get("APPDATA") or Path.home()) / "YARG-LIFX"
STATE_FILE = STATE_DIRECTORY / "tuya_devices.json"
SUPPORTED_PROTOCOLS = {"3.1", "3.2", "3.3", "3.4", "3.5"}
QR_LOGIN_TIMEOUT = 180
QR_POLL_INTERVAL = 2.0

RGB_COLORS: dict[str, tuple[int, int, int]] = {
    "RED": (255, 0, 0),
    "ORANGE": (255, 25, 0),
    "YELLOW": (255, 255, 0),
    "GREEN": (0, 255, 0),
    "CYAN": (0, 255, 255),
    "BLUE": (0, 0, 255),
    "PURPLE": (255, 0, 255),
    "WHITE": (255, 255, 255),
}

CAPABILITY_CODES = {
    "power": "switch_led",
    "mode": "work_mode",
    "brightness": "bright_value",
    "colour": "colour_data",
    "colourtemp": "temp_value",
}


def _quiet_provider_logs() -> None:
    for name in ("tuya_sharing", "tinytuya", "requests", "urllib3"):
        logging.getLogger(name).setLevel(logging.CRITICAL)


class TuyaSetupError(RuntimeError):
    """A user-safe Smart Life setup error that does not expose credentials."""


def advertised_capabilities(
    category: str,
    functions: dict[str, Any],
    local_strategy: dict[Any, Any],
) -> dict[str, int] | None:
    """Return verified Tuya DPS IDs for advertised, locally supported controls."""
    if category.casefold() != "dj":
        return None

    dps_by_code: dict[str, int] = {}
    for dp_id, mapping in local_strategy.items():
        if not isinstance(mapping, dict) or mapping.get("support_local", True) is False:
            continue
        code = mapping.get("status_code")
        if isinstance(code, str):
            try:
                dps_by_code[code] = int(dp_id)
            except (TypeError, ValueError):
                continue

    capabilities: dict[str, int] = {}
    for capability, code in CAPABILITY_CODES.items():
        if code not in functions or code not in dps_by_code:
            continue
        capabilities[capability] = dps_by_code[code]
    if "power" not in capabilities:
        return None
    return capabilities


def _plain_functions(device: Any) -> dict[str, Any]:
    raw = getattr(device, "function", {})
    if isinstance(raw, dict):
        return raw
    return {}


def _record_from_sdk_device(device: Any) -> dict[str, Any] | None:
    category = getattr(device, "category", "")
    local_key = getattr(device, "local_key", "")
    device_id = getattr(device, "id", "")
    if not isinstance(category, str) or not isinstance(device_id, str):
        return None
    if getattr(device, "support_local", False) is not True:
        return None

    functions = _plain_functions(device)
    local_strategy = getattr(device, "local_strategy", {})
    if not isinstance(local_strategy, dict):
        return None
    capabilities = advertised_capabilities(category, functions, local_strategy)
    if capabilities is None or not isinstance(local_key, str) or not local_key:
        return None

    return {
        "id": device_id,
        "name": str(getattr(device, "name", "") or device_id),
        "local_key": local_key,
        "category": category,
        "product_id": str(getattr(device, "product_id", "") or ""),
        "product_name": str(getattr(device, "product_name", "") or ""),
        "uuid": str(getattr(device, "uuid", "") or ""),
        "capabilities": capabilities,
        "protocol_version": None,
        "ip": None,
    }


def _save_records(records: list[dict[str, Any]]) -> None:
    STATE_DIRECTORY.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(
        {"schema_version": 1, "devices": records}, indent=2
    ) + "\n"
    temporary_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=STATE_DIRECTORY,
            prefix=".tuya-",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary.write(payload)
            temporary_path = temporary.name
        if getattr(os, "name", None) != "nt":
            os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, STATE_FILE)
    finally:
        if temporary_path is not None and os.path.exists(temporary_path):
            os.unlink(temporary_path)


def load_credentials() -> list[dict[str, Any]]:
    if not STATE_FILE.exists():
        return []
    try:
        stored = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if not isinstance(stored, dict) or stored.get("schema_version") != 1:
            raise ValueError("unsupported Tuya state format")
        records = stored.get("devices")
        if not isinstance(records, list):
            raise ValueError("Tuya device state is malformed")
        valid: list[dict[str, Any]] = []
        for record in records:
            if not isinstance(record, dict):
                continue
            device_id = record.get("id")
            local_key = record.get("local_key")
            category = record.get("category")
            raw_capabilities = record.get("capabilities")
            if (
                not isinstance(device_id, str)
                or not device_id
                or not isinstance(local_key, str)
                or not local_key.strip()
                or not isinstance(category, str)
                or category.casefold() != "dj"
                or not isinstance(raw_capabilities, dict)
            ):
                continue
            capabilities: dict[str, int] = {}
            for name, dp_id in raw_capabilities.items():
                if name not in CAPABILITY_CODES or isinstance(dp_id, bool):
                    continue
                if isinstance(dp_id, int):
                    normalized_dp = dp_id
                elif isinstance(dp_id, str) and dp_id.isdecimal():
                    normalized_dp = int(dp_id)
                else:
                    continue
                if normalized_dp > 0:
                    capabilities[name] = normalized_dp
            if "power" not in capabilities:
                continue
            normalized = dict(record)
            normalized["name"] = (
                record.get("name")
                if isinstance(record.get("name"), str) and record["name"].strip()
                else device_id
            )
            normalized["capabilities"] = capabilities
            valid.append(normalized)
        return valid
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        raise TuyaSetupError(
            f"Could not read the cached Smart Life device file ({type(exc).__name__})."
        ) from None


def logout() -> bool:
    """Delete only the cached Tuya account/device credentials."""
    try:
        STATE_FILE.unlink()
        return True
    except FileNotFoundError:
        return False


def begin_qr_login(user_code: str) -> tuple[str, str]:
    if not user_code.strip():
        raise TuyaSetupError("A Smart Life user code is required.")
    try:
        _quiet_provider_logs()
        from tuya_sharing import LoginControl

        response = LoginControl().qr_code(CLIENT_ID, SCHEMA, user_code.strip())
    except Exception as exc:
        raise TuyaSetupError(
            f"Could not start Smart Life QR login ({type(exc).__name__})."
        ) from None
    if not response.get("success") or not isinstance(
        response.get("result", {}).get("qrcode"), str
    ):
        code = response.get("code")
        suffix = f" (Tuya code {code})" if code is not None else ""
        raise TuyaSetupError(f"Smart Life did not issue a QR login token{suffix}.")
    token = response["result"]["qrcode"]
    return token, f"{QR_SCHEME}--qrLogin?token={token}"


def qr_matrix(payload: str) -> list[list[bool]]:
    """Return a bordered QR matrix for display without an image dependency."""
    import qrcode

    qr = qrcode.QRCode(border=4, box_size=1)
    qr.add_data(payload)
    qr.make(fit=True)
    return qr.get_matrix()


def print_qr(payload: str) -> None:
    import qrcode

    qr = qrcode.QRCode(border=2)
    qr.add_data(payload)
    qr.make(fit=True)
    qr.print_ascii(tty=True)


def _device_records_from_session(user_code: str, result: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        _quiet_provider_logs()
        from tuya_sharing import Manager

        endpoint = result.get("endpoint") or result.get("end_point")
        terminal_id = result.get("terminal_id")
        if not isinstance(endpoint, str) or not isinstance(terminal_id, str):
            raise TuyaSetupError("Tuya authorization did not include required session fields.")
        manager = Manager(
            CLIENT_ID,
            user_code,
            terminal_id,
            endpoint,
            result,
        )
        manager.update_device_cache()
        records: list[dict[str, Any]] = []
        for device in manager.device_map.values():
            record = _record_from_sdk_device(device)
            category = getattr(device, "category", "")
            name = str(getattr(device, "name", "") or getattr(device, "id", "unknown"))
            if record is not None:
                records.append(record)
                logger.info("Smart Life local light credentials retrieved for %s", name)
            elif category == "dj":
                logger.info(
                    "Smart Life light %s is unsupported for local control and was skipped",
                    name,
                )
            else:
                logger.info("Smart Life device %s is not a light and was ignored", name)
        _save_records(records)
        return records
    except TuyaSetupError:
        raise
    except Exception as exc:
        raise TuyaSetupError(
            f"Could not retrieve Smart Life device credentials ({type(exc).__name__})."
        ) from None


def connect_account(
    user_code: str,
    on_qr: Callable[[str], None],
    on_status: Callable[[str], None],
    cancel: threading.Event | None = None,
    timeout: float = QR_LOGIN_TIMEOUT,
) -> list[str]:
    """Run official sharing-SDK QR login, then cache only local light credentials."""
    token, payload = begin_qr_login(user_code)
    on_qr(payload)
    on_status("Scan the QR code in Smart Life and approve the login.")
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        if cancel is not None and cancel.is_set():
            raise TuyaSetupError("Smart Life login was cancelled.")
        try:
            _quiet_provider_logs()
            from tuya_sharing import LoginControl

            authorized, result = LoginControl().login_result(
                token, CLIENT_ID, user_code.strip()
            )
        except Exception as exc:
            raise TuyaSetupError(
                f"Could not check Smart Life authorization ({type(exc).__name__})."
            ) from None
        if authorized:
            on_status("Approved. Retrieving local device information...")
            records = _device_records_from_session(user_code.strip(), result)
            return [record["name"] for record in records]
        time.sleep(QR_POLL_INTERVAL)

    raise TuyaSetupError("Smart Life QR login timed out. Start a new login and scan promptly.")


@dataclass
class TuyaLight:
    device_id: str
    name: str
    ip: str
    protocol_version: str
    capabilities: dict[str, int]
    client: Any
    last_state: tuple[bool, str | None, int | None] | None = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    @property
    def identity(self) -> str:
        return self.device_id


def _matches_advertised_datapoints(
    record: dict[str, Any], client: Any, status: Any
) -> dict[str, int] | None:
    if not isinstance(status, dict) or not isinstance(status.get("dps"), dict):
        return None
    advertised = record.get("capabilities")
    if not isinstance(advertised, dict):
        return None
    detected = getattr(client, "dpset", {})
    if not isinstance(detected, dict) or not getattr(client, "bulb_configured", False):
        return None
    verified: dict[str, int] = {}
    reported_dps = {str(dp_id) for dp_id in status["dps"]}
    tiny_names = {
        "power": "switch",
        "mode": "mode",
        "brightness": "brightness",
        "colour": "colour",
        "colourtemp": "colourtemp",
    }
    for name, tiny_name in tiny_names.items():
        tiny_dp = detected.get(tiny_name)
        if tiny_dp is None:
            continue
        try:
            expected = int(advertised[name])
        except (KeyError, TypeError, ValueError):
            detected[tiny_name] = None
            continue
        if str(tiny_dp) == str(expected) and str(expected) in reported_dps:
            verified[name] = expected
        else:
            detected[tiny_name] = None
    if "power" not in verified:
        return None
    return verified


def _scan_lan(records: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    _quiet_provider_logs()
    # TinyTuya's public deviceScan wrapper cannot receive cached device keys.
    # Its scanner needs them to run a forced IP scan instead of falling back
    # to broadcast discovery only.
    from tinytuya import scanner
    import psutil

    _make_scanner_message_encoding_idempotent()
    devices = [
        {"id": record["id"], "key": record["local_key"], "name": record["name"]}
        for record in records
    ]
    # A UDP connect selects the operating system's default route without
    # sending a packet. Use its local address to avoid scanning inactive and
    # virtual adapter subnets reported by TinyTuya's automatic interface scan.
    route_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        route_socket.connect(("192.0.2.1", 9))
        local_address = route_socket.getsockname()[0]
    finally:
        route_socket.close()
    network: ipaddress.IPv4Network | None = None
    for addresses in psutil.net_if_addrs().values():
        for address in addresses:
            if (
                address.family == socket.AF_INET
                and address.address == local_address
                and address.netmask
            ):
                network = ipaddress.IPv4Interface(
                    f"{address.address}/{address.netmask}"
                ).network
                break
        if network is not None:
            break
    if network is None:
        raise RuntimeError("Could not determine the active local IPv4 network")

    results = scanner.devices(
        scantime=3,
        poll=False,
        forcescan=[str(network)],
        tuyadevices=devices,
        wantids=[record["id"] for record in records],
        assume_yes=True,
    )
    if not isinstance(results, dict):
        raise RuntimeError("TinyTuya returned an invalid LAN scan result")
    return results


def _make_scanner_message_encoding_idempotent() -> None:
    """Work around TinyTuya re-encoding queued wire bytes after socket retries."""
    module = importlib.import_module("tinytuya.core.XenonDevice")
    device_type = module.XenonDevice
    if getattr(device_type, "_yalc_bytes_queue_compatible", False):
        return

    original_encode = device_type._encode_message

    def encode_message(device: Any, message: Any) -> bytes:
        if isinstance(message, bytes):
            return message
        return original_encode(device, message)

    setattr(device_type, "_encode_message", encode_message)
    setattr(device_type, "_yalc_bytes_queue_compatible", True)


class TuyaController:
    """Cached Smart Life lights controlled by TinyTuya on the local network."""

    def __init__(self) -> None:
        self._all_lights: list[TuyaLight] = []
        self._lights: list[TuyaLight] = []
        self._commands: queue.Queue[LightingIntent | None] = queue.Queue(maxsize=1)
        self._submit_lock = threading.Lock()
        self._last_submitted: LightingIntent | None = None
        self._applied = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def discovered_devices(self) -> list[tuple[str, str]]:
        return [(light.name, light.ip) for light in self._lights]

    @property
    def all_discovered_devices(self) -> list[tuple[str, str]]:
        return [(light.name, light.ip) for light in self._all_lights]

    def start(self) -> None:
        self._discover()
        self._thread = threading.Thread(
            target=self._run, name="tuya-controller", daemon=True
        )
        self._thread.start()

    def scan(self) -> None:
        self._discover()

    def apply_filters(self) -> None:
        include = {item.casefold() for item in config.INCLUDE_LIGHTS}
        exclude = {item.casefold() for item in config.EXCLUDE_LIGHTS}
        self._lights = [
            light
            for light in self._all_lights
            if not any(
                value.casefold() in exclude
                for value in (light.name, light.ip, light.device_id)
            )
            and (
                not include
                or any(
                    value.casefold() in include
                    for value in (light.name, light.ip, light.device_id)
                )
            )
        ]

    def submit(self, intent: LightingIntent) -> None:
        with self._submit_lock:
            if intent == self._last_submitted:
                return
            try:
                self._commands.put_nowait(intent)
            except queue.Full:
                try:
                    self._commands.get_nowait()
                except queue.Empty:
                    pass
                self._commands.put_nowait(intent)
            self._last_submitted = intent
            self._applied.clear()

    def wait_until_applied(self, timeout: float) -> bool:
        return self._applied.wait(timeout)

    def identify(self, selector: str) -> None:
        normalized = selector.casefold()
        for light in self._all_lights:
            if normalized not in {
                light.name.casefold(),
                light.ip.casefold(),
                light.device_id.casefold(),
            }:
                continue
            with light.lock:
                try:
                    with rate_limited("tuya", light.device_id):
                        if "colour" in light.capabilities:
                            light.client.set_colour(0, 255, 255, nowait=True)
                        elif "brightness" in light.capabilities:
                            light.client.set_white_percentage(100, 0, nowait=True)
                        else:
                            light.client.turn_on(
                                switch=light.capabilities["power"], nowait=True
                            )
                    time.sleep(0.35)
                finally:
                    with rate_limited("tuya", light.device_id):
                        self._restore_state(light)
            return
        raise LookupError(f"No Smart Life light matches {selector!r}")

    def test_light_color(self, selector: str, color_name: str) -> None:
        normalized = selector.casefold()
        for light in self._all_lights:
            if normalized not in {
                light.name.casefold(),
                light.ip.casefold(),
                light.device_id.casefold(),
            }:
                continue
            intent = LightingIntent(color_name, transition_ms=0)
            multiplier = max(0.0, min(1.0, config.BRIGHTNESS_MULTIPLIER))
            brightness = max(1, min(100, round(multiplier * 100)))
            self._apply_light(light, intent, True, brightness)
            return
        raise LookupError(f"No Smart Life light matches {selector!r}")

    def rate_limit_key(self, selector: str) -> str:
        normalized = selector.casefold()
        for light in self._all_lights:
            if normalized in {
                light.name.casefold(),
                light.ip.casefold(),
                light.device_id.casefold(),
            }:
                return f"tuya:{light.device_id}".casefold()
        raise LookupError(f"No Smart Life light matches {selector!r}")

    def close(self) -> None:
        self._stop.set()
        try:
            self._commands.put_nowait(None)
        except queue.Full:
            try:
                self._commands.get_nowait()
            except queue.Empty:
                pass
            self._commands.put_nowait(None)
        if self._thread is not None:
            self._thread.join(timeout=3.0)
        self._close_lights()

    def _discover(self) -> None:
        self._close_lights()
        records = load_credentials()
        if not records:
            logger.info(
                "Smart Life / Tuya: no account linked. Use Devices > Connect Smart Life "
                "or run `python main.py --tuya-login`."
            )
            self._all_lights = []
            self._lights = []
            return

        try:
            lan_devices = _scan_lan(records)
        except Exception as exc:
            logger.warning("TinyTuya LAN discovery failed (%s)", type(exc).__name__)
            self._all_lights = []
            self._lights = []
            return

        lan_by_id: dict[str, tuple[str, dict[str, Any]]] = {}
        for ip_address, info in lan_devices.items():
            if not isinstance(ip_address, str) or not isinstance(info, dict):
                continue
            device_id = info.get("gwId") or info.get("id")
            if isinstance(device_id, str):
                lan_by_id[device_id] = (ip_address, info)

        lights: list[TuyaLight] = []
        updated_records: list[dict[str, Any]] = []
        for record in records:
            discovered = lan_by_id.get(record["id"])
            if discovered is None:
                logger.info("Smart Life light %s is not reachable on the LAN", record["name"])
                updated_records.append(record)
                continue
            ip_address, info = discovered
            version = str(info.get("version", ""))
            if version not in SUPPORTED_PROTOCOLS:
                logger.info(
                    "Smart Life light %s has an unsupported or unknown LAN protocol",
                    record["name"],
                )
                updated_records.append(record)
                continue
            client: Any | None = None
            try:
                import tinytuya

                client = tinytuya.BulbDevice(
                    record["id"],
                    address=ip_address,
                    local_key=record["local_key"],
                    version=float(version),
                    connection_timeout=2.0,
                    persist=True,
                )
                client.set_socketRetryLimit(1)
                status = client.status()
                capabilities = _matches_advertised_datapoints(
                    record, client, status
                )
                if capabilities is None:
                    client.close()
                    logger.info(
                        "Smart Life light %s has no verified TinyTuya light datapoint mapping",
                        record["name"],
                    )
                    updated_records.append(record)
                    continue
            except Exception as exc:
                if client is not None:
                    try:
                        client.close()
                    except Exception as close_exc:
                        logger.debug(
                            "Could not close failed TinyTuya client (%s)",
                            type(close_exc).__name__,
                        )
                logger.warning(
                    "Could not prepare Smart Life light %s on the LAN (%s)",
                    record["name"],
                    type(exc).__name__,
                )
                updated_records.append(record)
                continue

            light = TuyaLight(
                record["id"],
                record["name"],
                ip_address,
                version,
                capabilities,
                client,
            )
            lights.append(light)
            cached = dict(record)
            cached["ip"] = ip_address
            cached["protocol_version"] = version
            updated_records.append(cached)
            logger.info(
                "Smart Life light ready: %s (%s, protocol %s)",
                light.name,
                light.ip,
                light.protocol_version,
            )

        self._all_lights = lights
        self.apply_filters()
        if updated_records != records:
            try:
                _save_records(updated_records)
            except OSError as exc:
                logger.warning("Could not update cached Tuya LAN details (%s)", type(exc).__name__)
        if not self._lights:
            logger.info("Smart Life / Tuya: no compatible included lights found")

    def _run(self) -> None:
        intent: LightingIntent | None = None
        strobe_on = True
        next_strobe = 0.0
        while not self._stop.is_set():
            timeout = (
                max(0.0, next_strobe - time.monotonic())
                if intent is not None and intent.strobe_interval is not None
                else None
            )
            try:
                updated = self._commands.get(timeout=timeout)
            except queue.Empty:
                updated = None
            if updated is not None:
                intent = updated
                strobe_on = True
                self._apply(intent, strobe_on)
                self._applied.set()
                if intent.strobe_interval is not None:
                    next_strobe = time.monotonic() + intent.strobe_interval
            elif self._stop.is_set():
                break
            if (
                intent is not None
                and intent.strobe_interval is not None
                and time.monotonic() >= next_strobe
            ):
                strobe_on = not strobe_on
                self._apply(intent, strobe_on)
                next_strobe = time.monotonic() + intent.strobe_interval

    def _apply(self, intent: LightingIntent, strobe_on: bool) -> None:
        multiplier = max(0.0, min(1.0, config.BRIGHTNESS_MULTIPLIER))
        is_on = not intent.blackout and multiplier > 0 and (
            intent.strobe_interval is None or strobe_on
        )
        brightness = max(1, min(100, round(multiplier * 100)))
        for light in self._lights:
            try:
                self._apply_light(light, intent, is_on, brightness)
            except Exception as exc:
                logger.warning(
                    "Smart Life command failed for %s (%s)",
                    light.name,
                    type(exc).__name__,
                )
                light.last_state = None

    def _apply_light(
        self, light: TuyaLight, intent: LightingIntent, is_on: bool, brightness: int
    ) -> None:
        rgb = RGB_COLORS[intent.color]
        color_key: str | None = None
        brightness_key: int | None = None
        temperature = 0 if intent.color in {"ORANGE", "RED"} else 100
        if is_on and "colour" in light.capabilities:
            scaled = tuple(round(channel * brightness / 100) for channel in rgb)
            color_key = ",".join(str(channel) for channel in scaled)
        elif is_on and "brightness" in light.capabilities:
            brightness_key = brightness
            if "colourtemp" in light.capabilities:
                color_key = f"temperature:{temperature}"
        with light.lock:
            desired = (is_on, color_key, brightness_key)
            if light.last_state == desired:
                return
            with rate_limited("tuya", light.device_id):
                if not is_on:
                    light.client.turn_off(
                        switch=light.capabilities["power"], nowait=True
                    )
                elif "colour" in light.capabilities:
                    scaled_rgb = tuple(
                        round(channel * brightness / 100) for channel in rgb
                    )
                    light.client.set_colour(*scaled_rgb, nowait=True)
                elif "brightness" in light.capabilities:
                    light.client.set_white_percentage(
                        brightness,
                        temperature if "colourtemp" in light.capabilities else 0,
                        nowait=True,
                    )
                else:
                    light.client.turn_on(
                        switch=light.capabilities["power"], nowait=True
                    )
            light.last_state = desired

    def _restore_state(self, light: TuyaLight) -> None:
        state = light.last_state
        if state is None or not state[0]:
            light.client.turn_off(switch=light.capabilities["power"], nowait=True)
            return
        if "colour" in light.capabilities and state[1] is not None:
            rgb = tuple(int(value) for value in state[1].split(","))
            light.client.set_colour(*rgb, nowait=True)
        elif "brightness" in light.capabilities and state[2] is not None:
            temperature = (
                int(state[1].split(":", 1)[1])
                if "colourtemp" in light.capabilities
                and state[1] is not None
                and state[1].startswith("temperature:")
                else 0
            )
            light.client.set_white_percentage(
                state[2], temperature, nowait=True
            )
        else:
            light.client.turn_on(switch=light.capabilities["power"], nowait=True)

    def _close_lights(self) -> None:
        for light in self._all_lights:
            with light.lock:
                try:
                    light.client.close()
                except Exception as exc:
                    logger.debug(
                        "Could not close TinyTuya connection for %s (%s)",
                        light.name,
                        type(exc).__name__,
                    )
