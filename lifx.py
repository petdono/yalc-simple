"""LIFX LAN discovery and asynchronous-to-YARG lighting updates."""

from __future__ import annotations

import logging
import queue
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from lifxlan import LifxLAN

import config
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
        self._lights: list[Any] = []
        self._labels: dict[str, str] = {}
        self._last_sent: dict[str, tuple[bool, tuple[int, int, int, int] | None]] = {}
        self._offline: set[str] = set()
        self._commands: queue.Queue[LightingIntent | None] = queue.Queue(maxsize=1)
        self._submit_lock = threading.Lock()
        self._last_submitted: LightingIntent | None = None
        self._applied = threading.Event()
        self._stop = threading.Event()
        self._rediscover = threading.Event()
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

        previous_ids = {self._light_id(light) for light in self._lights}
        lights: list[Any] = []
        labels: dict[str, str] = {}
        include = {value.casefold() for value in config.INCLUDE_LIGHTS}

        for light in discovered:
            light_id = self._light_id(light)
            try:
                label = str(light.get_label())
            except Exception as exc:
                label = "Unknown label"
                logger.warning("Could not read label from LIFX light %s: %s", light_id, exc)
            try:
                ip_address = str(light.get_ip_addr())
            except Exception as exc:
                ip_address = "Unknown IP"
                logger.warning("Could not read IP from LIFX light %s: %s", light_id, exc)

            logger.info("Discovered LIFX light: %s (%s)", label, ip_address)
            if include and label.casefold() not in include and ip_address.casefold() not in include:
                logger.info("Skipping LIFX light not in INCLUDE_LIGHTS: %s", label)
                continue

            lights.append(light)
            labels[light_id] = label

        current_ids = {self._light_id(light) for light in lights}
        self._offline.update(previous_ids - current_ids)
        for light_id in current_ids & self._offline:
            self._last_sent.pop(light_id, None)
            self._offline.discard(light_id)

        self._lights = lights
        self._labels = labels
        if not lights:
            logger.warning("No included LIFX lights found")

    def _run(self) -> None:
        next_periodic_discovery = (
            time.monotonic() + config.LIFX_REDISCOVERY_INTERVAL
        )
        next_retry_discovery = 0.0
        intent: LightingIntent | None = None
        strobe_on = True
        next_strobe = 0.0

        while not self._stop.is_set():
            now = time.monotonic()
            if self._rediscover.is_set() and now >= next_retry_discovery:
                self._discover()
                self._rediscover.clear()
                next_retry_discovery = time.monotonic() + 5.0
                next_periodic_discovery = (
                    time.monotonic() + config.LIFX_REDISCOVERY_INTERVAL
                )
                if intent is not None:
                    self._apply(intent, strobe_on)
            elif now >= next_periodic_discovery:
                self._discover()
                next_periodic_discovery = (
                    time.monotonic() + config.LIFX_REDISCOVERY_INTERVAL
                )
                if intent is not None:
                    self._apply(intent, strobe_on)

            deadlines = [next_periodic_discovery]
            if self._rediscover.is_set():
                deadlines.append(next_retry_discovery)
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
            # _apply_to_light logs an individual failure and schedules discovery.
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
            self._rediscover.set()
