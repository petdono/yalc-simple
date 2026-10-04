"""Local Govee LAN discovery and asynchronous lighting updates."""

from __future__ import annotations

import json
import logging
import queue
import socket
import threading
import time
from dataclasses import dataclass
from typing import Any

import config
from yarg import LightingIntent

logger = logging.getLogger(__name__)

MULTICAST_GROUP = "239.255.255.250"
DISCOVERY_PORT = 4001
LISTEN_PORT = 4002
CONTROL_PORT = 4003

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


@dataclass(frozen=True)
class GoveeDevice:
    ip: str
    device_id: str
    sku: str
    name: str

    @property
    def identity(self) -> str:
        return self.device_id or self.ip


def encode_message(command: str, data: dict[str, Any]) -> bytes:
    return json.dumps(
        {"msg": {"cmd": command, "data": data}}, separators=(",", ":")
    ).encode("utf-8")


def scan_message() -> bytes:
    return encode_message("scan", {"account_topic": "reserve"})


def color_message(rgb: tuple[int, int, int]) -> bytes:
    red, green, blue = rgb
    return encode_message(
        "colorwc",
        {
            "color": {"r": red, "g": green, "b": blue},
            "colorTemInKelvin": 0,
        },
    )


def color_temperature_message(kelvin: int) -> bytes:
    if not 2000 <= kelvin <= 9000:
        raise ValueError("Govee color temperature must be between 2000 and 9000 K.")
    return encode_message(
        "colorwc",
        {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": kelvin},
    )


def brightness_message(percent: int) -> bytes:
    if not 1 <= percent <= 100:
        raise ValueError("Govee brightness must be between 1 and 100 percent.")
    return encode_message("brightness", {"value": percent})


def power_message(on: bool) -> bytes:
    return encode_message("turn", {"value": 1 if on else 0})


def parse_discovery_response(packet: bytes, source_ip: str) -> GoveeDevice | None:
    try:
        message = json.loads(packet.decode("utf-8"))
        if not isinstance(message, dict):
            return None
        body = message.get("msg")
        if not isinstance(body, dict):
            return None
        if body.get("cmd") != "scan":
            return None
        data = body.get("data")
        if not isinstance(data, dict):
            return None
        device_id = data.get("device")
        sku = data.get("sku")
        if not isinstance(device_id, str) or not isinstance(sku, str):
            return None
        name = data.get("deviceName")
        if not isinstance(name, str) or not name.strip():
            name = sku
        return GoveeDevice(
            ip=source_ip,
            device_id=device_id,
            sku=sku,
            name=name.strip(),
        )
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None


@dataclass
class _SentState:
    power: bool | None = None
    color: tuple[int, int, int] | None = None
    brightness: int | None = None


class GoveeController:
    def __init__(self) -> None:
        self._devices: list[GoveeDevice] = []
        self._states: dict[str, _SentState] = {}
        self._commands: queue.Queue[LightingIntent | None] = queue.Queue(maxsize=1)
        self._submit_lock = threading.Lock()
        self._last_submitted: LightingIntent | None = None
        self._applied = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @property
    def discovered_devices(self) -> list[tuple[str, str]]:
        return [(device.name, device.ip) for device in self._devices]

    def start(self) -> None:
        self._discover()
        self._thread = threading.Thread(
            target=self._run, name="govee-controller", daemon=True
        )
        self._thread.start()

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
                try:
                    self._commands.put_nowait(intent)
                except queue.Full:
                    logger.warning("Dropped Govee lighting update because the queue is full")
                    return
            self._last_submitted = intent
            self._applied.clear()

    def wait_until_applied(self, timeout: float) -> bool:
        return self._applied.wait(timeout)

    def set_color_temperature(self, ip: str, kelvin: int) -> None:
        """Send a LAN color-temperature command to a tunable-white Govee device."""
        self._send(ip, color_temperature_message(kelvin))

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
            self._thread.join(timeout=2.0)

    def _discover(self) -> None:
        discovered: dict[str, GoveeDevice] = {}
        listener: socket.socket | None = None
        try:
            listener = socket.socket(
                socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP
            )
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            listener.bind(("", LISTEN_PORT))
            listener.sendto(scan_message(), (MULTICAST_GROUP, DISCOVERY_PORT))
            deadline = time.monotonic() + config.GOVEE_DISCOVERY_TIMEOUT
            while not self._stop.is_set():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                listener.settimeout(min(0.25, remaining))
                try:
                    packet, address = listener.recvfrom(4096)
                except socket.timeout:
                    continue
                device = parse_discovery_response(packet, address[0])
                if device is not None:
                    discovered[device.identity] = device
        except OSError as exc:
            logger.warning("Govee LAN discovery failed: %s", exc)
        finally:
            if listener is not None:
                listener.close()

        include = {item.casefold() for item in config.GOVEE_INCLUDE_DEVICES}
        devices = [
            device
            for device in discovered.values()
            if not include
            or any(
                value.casefold() in include
                for value in (device.ip, device.device_id, device.sku)
            )
        ]
        previous_devices = {device.identity: device for device in self._devices}
        previous_ids = set(previous_devices)
        current_ids = {device.identity for device in devices}
        for identity in previous_ids - current_ids:
            self._states.pop(identity, None)
        for device in devices:
            previous = previous_devices.get(device.identity)
            if previous is None or previous.ip != device.ip:
                self._states.pop(device.identity, None)
        self._devices = devices
        if not devices:
            logger.info("Govee LAN discovery: no compatible included devices found")

    @staticmethod
    def _send(ip: str, payload: bytes) -> None:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP) as sender:
            sender.sendto(payload, (ip, CONTROL_PORT))

    def _run(self) -> None:
        next_discovery = time.monotonic() + config.LIFX_REDISCOVERY_INTERVAL + config.LIFX_REDISCOVERY_INTERVAL
        intent: LightingIntent | None = None
        strobe_on = True
        next_strobe = 0.0
        while not self._stop.is_set():
            now = time.monotonic()
            if now >= next_discovery:
                self._discover()
                if intent is not None:
                    self._apply(intent, strobe_on)
                next_discovery = time.monotonic() + config.LIFX_REDISCOVERY_INTERVAL

            deadlines = [next_discovery]
            if intent is not None and intent.strobe_interval is not None:
                deadlines.append(next_strobe)
            timeout = max(0.0, min(deadlines) - time.monotonic())
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
        rgb = RGB_COLORS[intent.color]
        brightness = max(
            1, min(100, round(multiplier * 100))
        )
        for device in self._devices:
            self._apply_device(device, is_on, rgb, brightness)

    def _apply_device(
        self,
        device: GoveeDevice,
        is_on: bool,
        rgb: tuple[int, int, int],
        brightness: int,
    ) -> None:
        state = self._states.setdefault(device.identity, _SentState())
        try:
            if not is_on:
                if state.power is not False:
                    self._send(device.ip, power_message(False))
                    state.power = False
                return

            if state.color != rgb:
                self._send(device.ip, color_message(rgb))
                state.color = rgb
            if state.brightness != brightness:
                self._send(device.ip, brightness_message(brightness))
                state.brightness = brightness
            if state.power is not True:
                self._send(device.ip, power_message(True))
                state.power = True
        except OSError as exc:
            logger.warning("Govee command failed for %s (%s): %s", device.name, device.ip, exc)
            self._states.pop(device.identity, None)
