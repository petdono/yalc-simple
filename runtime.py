"""Background YARG UDP receiver and LIFX bridge lifecycle."""

from __future__ import annotations

import logging
import socket
import threading

import config
from lifx import LifxController
from yarg import (
    LightingIntent,
    describe_changes,
    intent_for_state,
    parse_packet,
)


class YargBridge:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._ready = threading.Event()
        self._thread: threading.Thread | None = None
        self._controller: LifxController | None = None
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
            raise TimeoutError("Bridge is not ready; LIFX discovery is still running")
        with self._controller_lock:
            controller = self._controller
        if controller is None:
            raise RuntimeError("LIFX controller is unavailable")
        controller.submit(LightingIntent(color, transition_ms=250))
        if not controller.wait_until_applied(timeout):
            raise TimeoutError(f"Timed out waiting for the {color.lower()} test color")
        return True

    def _run(self) -> None:
        receiver = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        controller: LifxController | None = None
        try:
            receiver.bind(("0.0.0.0", config.YARG_UDP_PORT))
            receiver.settimeout(0.25)
            logging.info(
                "Listening for YARG UDP datagrams on port %d", config.YARG_UDP_PORT
            )

            controller = LifxController()
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
    controller = LifxController()
    try:
        controller.start()
        controller.submit(LightingIntent(color, transition_ms=250))
        if not controller.wait_until_applied(10.0):
            logging.error("Timed out waiting for the LIFX test-color command")
            return 1
        return 0
    finally:
        controller.close()

