"""Background YARG UDP receiver and multi-brand lighting lifecycle."""

from __future__ import annotations

import logging
import socket
import threading
import time

import config
from govee import GoveeController
from lifx import LifxController
from tuya import TuyaController
from yarg import (
    LightingIntent,
    describe_changes,
    intent_for_state,
    parse_packet,
)


class LightingOutputs:
    """Fan out each common YARG lighting intent to the enabled brands."""

    def __init__(self) -> None:
        self.lifx = LifxController() if config.LIFX_ENABLED else None
        self.govee = GoveeController() if config.GOVEE_ENABLED else None
        self.tuya = TuyaController() if config.TUYA_ENABLED else None
        self._backends: list[LifxController | GoveeController | TuyaController] = []

    def start(self) -> None:
        candidates: list[LifxController | GoveeController | TuyaController] = []
        if self.lifx is not None:
            candidates.append(self.lifx)
        if self.govee is not None:
            candidates.append(self.govee)
        if self.tuya is not None:
            candidates.append(self.tuya)
        for backend in candidates:
            try:
                backend.start()
                self._backends.append(backend)
            except Exception:
                logging.exception("Could not start %s lighting output", type(backend).__name__)
                try:
                    backend.close()
                except Exception:
                    logging.exception(
                        "Error closing failed %s output", type(backend).__name__
                    )
        self._log_discovered_lights()

    def scan(self) -> None:
        if self.lifx is not None:
            self.lifx.scan()
        if self.govee is not None:
            self.govee.scan()
        if self.tuya is not None:
            self.tuya.scan()
        self._log_discovered_lights()

    def apply_filters(self) -> None:
        if self.lifx is not None:
            self.lifx.apply_filters()
        if self.govee is not None:
            self.govee.apply_filters()
        if self.tuya is not None:
            self.tuya.apply_filters()
        self._log_discovered_lights()

    def discovered_devices(self) -> list[tuple[str, str, str]]:
        devices: list[tuple[str, str, str]] = []
        for provider, backend in (
            ("LIFX", self.lifx),
            ("Govee", self.govee),
            ("Tuya", self.tuya),
        ):
            if backend is not None:
                devices.extend(
                    (provider, label, ip_address)
                    for label, ip_address in backend.discovered_devices
                )
        return devices

    def all_discovered_devices(self) -> list[tuple[str, str, str]]:
        devices: list[tuple[str, str, str]] = []
        for provider, backend in (
            ("LIFX", self.lifx),
            ("Govee", self.govee),
            ("Tuya", self.tuya),
        ):
            if backend is not None:
                devices.extend(
                    (provider, label, ip_address)
                    for label, ip_address in backend.all_discovered_devices
                )
        return devices

    def identify(self, provider: str, selector: str) -> None:
        backend: LifxController | GoveeController | TuyaController | None = {
            "LIFX": self.lifx,
            "Govee": self.govee,
            "Tuya": self.tuya,
        }.get(provider)
        if backend is None:
            raise RuntimeError(f"{provider} lighting output is disabled")
        backend.identify(selector)

    def test_light_color(self, provider: str, selector: str, color: str) -> None:
        backend: LifxController | GoveeController | TuyaController | None = {
            "LIFX": self.lifx,
            "Govee": self.govee,
            "Tuya": self.tuya,
        }.get(provider)
        if backend is None:
            raise RuntimeError(f"{provider} lighting output is disabled")
        backend.test_light_color(selector, color)

    def rate_limit_key(self, provider: str, selector: str) -> str:
        backend: LifxController | GoveeController | TuyaController | None = {
            "lifx": self.lifx,
            "govee": self.govee,
            "tuya": self.tuya,
        }.get(provider.casefold())
        if backend is None:
            raise ValueError(f"{provider} lighting output is disabled")
        return backend.rate_limit_key(selector)

    def submit(self, intent: LightingIntent) -> None:
        for backend in self._backends:
            try:
                backend.submit(intent)
            except Exception:
                logging.exception("Could not submit YARG state to %s", type(backend).__name__)

    def wait_until_applied(self, timeout: float) -> bool:
        if not self._backends:
            return False
        deadline = time.monotonic() + timeout
        applied = True
        for backend in self._backends:
            remaining = max(0.0, deadline - time.monotonic())
            try:
                applied = backend.wait_until_applied(remaining) and applied
            except Exception:
                logging.exception("Could not confirm output from %s", type(backend).__name__)
                applied = False
        return applied

    def close(self) -> None:
        for backend in self._backends:
            try:
                backend.close()
            except Exception:
                logging.exception("Error closing %s lighting output", type(backend).__name__)
        self._backends.clear()

    def _log_discovered_lights(self) -> None:
        lifx_devices = (
            self.lifx.discovered_devices if self.lifx is not None else []
        )
        govee_devices = (
            self.govee.discovered_devices if self.govee is not None else []
        )
        tuya_devices = (
            self.tuya.discovered_devices if self.tuya is not None else []
        )
        logging.info("Discovered lights:")
        logging.info("LIFX")
        for label, ip_address in lifx_devices:
            logging.info("  %s - %s", label, ip_address)
        logging.info("Govee%s", "" if self.govee is not None else " (disabled)")
        for label, ip_address in govee_devices:
            logging.info("  %s - %s", label, ip_address)
        logging.info("Smart Life / Tuya%s", "" if self.tuya is not None else " (disabled)")
        for label, ip_address in tuya_devices:
            logging.info("  %s - %s", label, ip_address)
        logging.info(
            "%d lights ready.",
            len(lifx_devices) + len(govee_devices) + len(tuya_devices),
        )


class YargBridge:
    def __init__(self, lighting_outputs: LightingOutputs | None = None) -> None:
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._provided_outputs = lighting_outputs
        self._controller: LightingOutputs | None = lighting_outputs
        self._controller_lock = threading.Lock()

    @property
    def ready(self) -> threading.Event:
        return self._ready

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    @property
    def lighting_outputs(self) -> LightingOutputs | None:
        with self._controller_lock:
            return self._controller

    def start(self) -> None:
        if self.running:
            return
        self._stop.clear()
        self._ready.clear()
        self._thread = threading.Thread(
            target=self._run, name="yarg-lifx-bridge", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 4.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            if self._thread.is_alive():
                logging.warning("Bridge is still shutting down")

    def test_color(self, color: str, timeout: float = 10.0) -> bool:
        if not self._ready.wait(timeout):
            raise TimeoutError("Bridge is not ready; lighting discovery is still running")
        with self._controller_lock:
            controller = self._controller
        if controller is None:
            raise RuntimeError("Lighting outputs are unavailable")
        controller.submit(LightingIntent(color, transition_ms=250))
        if not controller.wait_until_applied(timeout):
            raise TimeoutError(f"Timed out waiting for the {color.lower()} test color")
        return True

    def _run(self) -> None:
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        controller: LightingOutputs | None = None
        try:
            receiver.bind(("0.0.0.0", config.YARG_UDP_PORT))
            receiver.settimeout(0.25)
            logging.info(
                "Listening for YARG UDP datagrams on port %d", config.YARG_UDP_PORT
            )

            controller = self._provided_outputs or LightingOutputs()
            with self._controller_lock:
                self._controller = controller
            if self._provided_outputs is None:
                controller.start()
            self._ready.set()
            previous = None
            first_valid_packet = True
            while not self._stop.is_set():
                try:
                    packet, source = receiver.recvfrom(2048)
                except socket.timeout:
                    continue
                try:
                    state = parse_packet(packet)
                except ValueError as exc:
                    logging.warning(
                        "Ignoring malformed UDP packet from %s: %s", source, exc
                    )
                    continue

                if first_valid_packet:
                    logging.info(
                        "Received first valid YARG UDP datagram from %s "
                        "(%d bytes, protocol v%d)",
                        source,
                        len(packet),
                        state.version,
                    )
                    first_valid_packet = False
                if config.DEBUG_LOGGING:
                    for line in describe_changes(previous, state):
                        logging.info("%s", line)
                controller.submit(
                    intent_for_state(
                        state, transition_ms=config.COLOR_TRANSITION_MS
                    )
                )
                previous = state
        except OSError as exc:
            logging.error("Could not start YARG UDP listener: %s", exc)
        except Exception:
            logging.exception("YARG-LIFX bridge stopped unexpectedly")
        finally:
            self._ready.clear()
            receiver.close()
            if controller is not None and self._provided_outputs is None:
                controller.close()
            with self._controller_lock:
                self._controller = self._provided_outputs
            logging.info("YARG-LIFX bridge stopped")


def run_test(color: str = "BLUE") -> int:
    controller = LightingOutputs()
    try:
        controller.start()
        controller.submit(LightingIntent(color, transition_ms=250))
        if not controller.wait_until_applied(10.0):
            logging.error("Timed out waiting for the lighting test-color command")
            return 1
        return 0
    finally:
        controller.close()
