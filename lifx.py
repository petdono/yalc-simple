"""LIFX LAN discovery and asynchronous-to-YARG lighting updates."""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from lifxlan import Light, LifxLAN

import config
from rate_limits import rate_limited
from yarg import LightingIntent

logger = logging.getLogger(__name__)

# HSBK values use the 0..65535 ranges expected by lifxlan.
COLORS = {
    "RED": (65535, 65535, 65535, 3500),
    "ORANGE": (6500, 65535, 65535, 3500),
    "YELLOW": (9000, 65535, 65535, 3500),
    "GREEN": (16173, 65535, 65535, 3500),
    "CYAN": (29814, 65535, 65535, 3500),
    "BLUE": (43634, 65535, 65535, 3500),
    "PURPLE": (50486, 65535, 65535, 3500),
    "WHITE": (58275, 0, 65535, 5500),
}


class TimedDiscoveryLAN(LifxLAN):
    """Use the configured timeout for the library's broadcast discovery call."""

    def __init__(self, timeout: float) -> None:
        super().__init__()
        self.discovery_timeout = timeout

    def broadcast_with_resp(
        self,
        msg_type: Any,
        response_type: Any,
        payload: dict[str, Any] | None = None,
        timeout_secs: float | None = None,
        max_attempts: int | None = None,
    ) -> Any:
        return super().broadcast_with_resp(
            msg_type,
            response_type,
            {} if payload is None else payload,
            self.discovery_timeout if timeout_secs is None else timeout_secs,
            1 if max_attempts is None else max_attempts,
        )


class LifxController:
    def __init__(self) -> None:
        self._lan = TimedDiscoveryLAN(config.LIFX_DISCOVERY_TIMEOUT)
        self._all_lights: list[Any] = []
        self._all_labels: dict[str, str] = {}
        self._all_ips: dict[str, str] = {}
        self._manual_light_ids: set[str] = set()
        self._lights: list[Any] = []
        self._labels: dict[str, str] = {}
        self._ips: dict[str, str] = {}
        self._last_sent: dict[str, tuple[bool, tuple[int, int, int, int] | None]] = {}
        self._offline: set[str] = set()
        self._commands: queue.Queue[LightingIntent | None] = queue.Queue(maxsize=1)
        self._submit_lock = threading.Lock()
        self._last_submitted: LightingIntent | None = None
        self._applied = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._executor = ThreadPoolExecutor(
            max_workers=8, thread_name_prefix="lifx-command"
        )

    def start(self) -> None:
        self._discover()
        self._thread = threading.Thread(
            target=self._run, name="lifx-controller", daemon=True
        )
        self._thread.start()

    def scan(self) -> None:
        self._discover()

    def apply_filters(self) -> None:
        previous_ids = {self._light_id(light) for light in self._lights}
        include = {value.casefold() for value in config.INCLUDE_LIGHTS}
        exclude = {value.casefold() for value in config.EXCLUDE_LIGHTS}
        lights: list[Any] = []
        for light in self._all_lights:
            light_id = self._light_id(light)
            label = self._all_labels[light_id]
            ip_address = self._all_ips[light_id]
            if any(
                value in exclude
                for value in (label.casefold(), ip_address.casefold(), light_id.casefold())
            ):
                continue
            if (
                light_id.casefold() not in self._manual_light_ids
                and include
                and label.casefold() not in include
                and ip_address.casefold() not in include
            ):
                continue
            lights.append(light)

        current_ids = {self._light_id(light) for light in lights}
        self._offline.update(previous_ids - current_ids)
        for light_id in current_ids - previous_ids:
            self._last_sent.pop(light_id, None)
            self._offline.discard(light_id)
        self._lights = lights
        self._labels = {
            self._light_id(light): self._all_labels[self._light_id(light)]
            for light in lights
        }
        self._ips = {
            self._light_id(light): self._all_ips[self._light_id(light)]
            for light in lights
        }

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
                    logger.warning("Dropped a lighting update because the queue is full")
                    return
            self._last_submitted = intent
            self._applied.clear()

    def wait_until_applied(self, timeout: float) -> bool:
        return self._applied.wait(timeout)

    @property
    def discovered_devices(self) -> list[tuple[str, str]]:
        devices: list[tuple[str, str]] = []
        for light in self._lights:
            light_id = self._light_id(light)
            devices.append(
                (
                    self._labels.get(light_id, "Unknown label"),
                    self._ips.get(light_id, "Unknown IP"),
                )
            )
        return devices

    @property
    def all_discovered_devices(self) -> list[tuple[str, str]]:
        return [
            (
                self._all_labels.get(self._light_id(light), "Unknown label"),
                self._all_ips.get(self._light_id(light), "Unknown IP"),
            )
            for light in self._all_lights
        ]

    def identify(self, selector: str) -> None:
        """Briefly flash one light, then restore its prior color and power."""
        normalized = selector.casefold()
        for light in self._all_lights:
            light_id = self._light_id(light)
            label = self._all_labels.get(light_id, "")
            ip_address = self._all_ips.get(light_id, "")
            if normalized not in {
                light_id.casefold(),
                label.casefold(),
                ip_address.casefold(),
            }:
                continue
            original_color = tuple(light.get_color())
            was_on = light.get_power() > 0
            try:
                light.set_color(COLORS["CYAN"], duration=100, rapid=True)
                light.set_power(True, rapid=True)
                time.sleep(0.35)
            finally:
                try:
                    light.set_color(original_color, duration=100, rapid=True)
                finally:
                    light.set_power(was_on, rapid=True)
            return
        raise LookupError(f"No LIFX light matches {selector!r}")

    def test_light_color(self, selector: str, color_name: str) -> None:
        normalized = selector.casefold()
        for light in self._all_lights:
            light_id = self._light_id(light)
            if normalized not in {
                light_id.casefold(),
                self._all_labels.get(light_id, "").casefold(),
                self._all_ips.get(light_id, "").casefold(),
            }:
                continue
            hue, saturation, brightness, kelvin = COLORS[color_name]
            brightness = round(
                brightness * max(0.0, min(1.0, config.BRIGHTNESS_MULTIPLIER))
            )
            with rate_limited("lifx", light_id):
                light.set_color(
                    (hue, saturation, brightness, kelvin), duration=0, rapid=True
                )
                light.set_power(True, rapid=True)
            self._last_sent[light_id] = (
                True,
                (hue, saturation, brightness, kelvin),
            )
            self._offline.discard(light_id)
            return
        raise LookupError(f"No LIFX light matches {selector!r}")

    def rate_limit_key(self, selector: str) -> str:
        normalized = selector.casefold()
        for light in self._all_lights:
            light_id = self._light_id(light)
            if normalized in {
                light_id.casefold(),
                self._all_labels.get(light_id, "").casefold(),
                self._all_ips.get(light_id, "").casefold(),
            }:
                return f"lifx:{light_id}".casefold()
        raise LookupError(f"No LIFX light matches {selector!r}")

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
            self._thread.join()
        self._executor.shutdown(wait=True)

    @staticmethod
    def _light_id(light: Any) -> str:
        try:
            return str(light.get_mac_addr())
        except Exception:
            return str(id(light))

    def _discover(self) -> None:
        try:
            discovered = self._lan.get_lights()
        except Exception as exc:
            logger.error("LIFX discovery failed: %s", exc)
            return

        manual_by_mac = {
            item[3].casefold(): item
            for item in config.MANUAL_LIGHTS
            if item[0] == "lifx"
        }
        manual_lights = [
            Light(item[3], item[2], source_id=self._lan.source_id)
            for item in manual_by_mac.values()
        ]
        discovered_by_id = {self._light_id(light): light for light in discovered}
        discovered_by_id.update(
            {self._light_id(light): light for light in manual_lights}
        )
        all_lights: list[Any] = []
        labels: dict[str, str] = {}
        ips: dict[str, str] = {}

        for light in discovered_by_id.values():
            light_id = self._light_id(light)
            manual = manual_by_mac.get(light_id.casefold())
            if manual is not None:
                label, ip_address = manual[1], manual[2]
            else:
                try:
                    label = str(light.get_label())
                except Exception as exc:
                    label = "Unknown label"
                    logger.warning(
                        "Could not read label from LIFX light %s: %s", light_id, exc
                    )
                try:
                    ip_address = str(light.get_ip_addr())
                except Exception as exc:
                    ip_address = "Unknown IP"
                    logger.warning(
                        "Could not read IP from LIFX light %s: %s", light_id, exc
                    )

            logger.info("Discovered LIFX light: %s (%s)", label, ip_address)
            all_lights.append(light)
            labels[light_id] = label
            ips[light_id] = ip_address

        self._all_lights = all_lights
        self._all_labels = labels
        self._all_ips = ips
        self._manual_light_ids = set(manual_by_mac)
        self.apply_filters()
        if not self._lights:
            logger.warning("No included LIFX lights found")

    def _run(self) -> None:
        intent: LightingIntent | None = None
        strobe_on = True
        next_strobe = 0.0

        while not self._stop.is_set():
            deadlines: list[float] = []
            if intent is not None and intent.strobe_interval is not None:
                deadlines.append(next_strobe)
            timeout = (
                max(0.0, min(deadlines) - time.monotonic())
                if deadlines
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
                if intent.strobe_interval is not None:
                    next_strobe = time.monotonic() + intent.strobe_interval
                self._applied.set()
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
        is_on = not intent.blackout and (
            intent.strobe_interval is None or strobe_on
        )
        color: tuple[int, int, int, int] | None = None
        if is_on:
            hue, saturation, brightness, kelvin = COLORS[intent.color]
            brightness = round(
                brightness * max(0.0, min(1.0, config.BRIGHTNESS_MULTIPLIER))
            )
            color = (hue, saturation, brightness, kelvin)

        futures = [
            self._executor.submit(self._apply_to_light, light, intent, is_on, color)
            for light in self._lights
        ]
        for future in as_completed(futures):
            # _apply_to_light logs individual device failures.
            future.result()

    def _apply_to_light(
        self,
        light: Any,
        intent: LightingIntent,
        is_on: bool,
        color: tuple[int, int, int, int] | None,
    ) -> None:
        light_id = self._light_id(light)
        desired = (is_on, color)
        if self._last_sent.get(light_id) == desired:
            return

        try:
            with rate_limited("lifx", light_id):
                if is_on and color is not None:
                    duration = (
                        0
                        if intent.strobe_interval is not None
                        else intent.transition_ms
                    )
                    light.set_color(color, duration=duration, rapid=True)
                    previous = self._last_sent.get(light_id)
                    if previous is None or not previous[0]:
                        light.set_power(True, rapid=True)
                else:
                    light.set_power(False, rapid=True)
                self._last_sent[light_id] = desired
                self._offline.discard(light_id)
        except Exception as exc:
            label = self._labels.get(light_id, light_id)
            logger.warning("LIFX command failed for %s: %s", label, exc)
            self._offline.add(light_id)
