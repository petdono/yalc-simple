"""Background YARG UDP receiver and multi-brand lighting lifecycle."""

from __future__ import annotations

import logging
import socket
import threading
import time

import config
from govee import GoveeController
from lifx import LifxController
from yarg import (
    LightingIntent,
    describe_changes,
    intent_for_state,
    parse_packet,
)


class LightingOutputs:
    """Fan out each common YARG lighting intent to the enabled brands."""

    def __init__(self) -> None:
        self.lifx = LifxController()
        self.govee = GoveeController() if config.GOVEE_ENABLED else None
        self._backends: list[LifxController | GoveeController] = []

    def start(self) -> None:
        candidates: list[LifxController | GoveeController] = [self.lifx]
        if self.govee is not None:
            candidates.append(self.govee)
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

    def submit(self, intent: LightingIntent) -> None:
        for backend in self._backends:
            try:
                backend.submit(intent)
            except Exception:
                logging.exception("Could not submit YARG state to %s", type(backend).__name__)

    def wait_until_applied(self, timeout: float) -> bool:
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
        lifx_devices = self.lifx.discovered_devices
        govee_devices = (
            self.govee.discovered_devices if self.govee is not None else []
        )
        logging.info("Discovered lights:")
        logging.info("LIFX")
        for label, ip_address in lifx_devices:
            logging.info("  %s - %s", label, ip_address)
        logging.info("Govee%s", "" if self.govee is not None else " (disabled)")
        for label, ip_address in govee_devices:
            logging.info("  %s - %s", label, ip_address)
        logging.info("%d lights ready.", len(lifx_devices) + len(govee_devices))


class YargBridge:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._controller: LightingOutputs | None = None
        self._controller_lock = threading.Lock()

    @property
    def ready(self) -> threading.Event:
        return self._ready

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

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

            controller = LightingOutputs()
            with self._controller_lock:
                self._controller = controller
            controller.start()
            self._ready.set()
            previous = None
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
            if controller is not None:
                controller.close()
            with self._controller_lock:
                self._controller = None
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
