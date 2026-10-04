"""Small Tkinter configuration and control window."""

from __future__ import annotations

import json
import logging
import os
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox, ttk
from typing import Any

import config
from lifx import COLORS
from runtime import YargBridge, run_test

logger = logging.getLogger(__name__)
SETTINGS_DIRECTORY = Path(os.environ.get("APPDATA", Path.home())) / "YARG-LIFX"
SETTINGS_FILE = SETTINGS_DIRECTORY / "settings.json"


class QueueLogHandler(logging.Handler):
    def __init__(self, records: queue.Queue[str]) -> None:
        super().__init__()
        self.records = records
        self.setFormatter(logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S"))

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.records.put_nowait(self.format(record))
        except Exception:
            self.handleError(record)


def _current_settings() -> dict[str, Any]:
    return {
        "yarg_udp_port": config.YARG_UDP_PORT,
        "lifx_discovery_timeout": config.LIFX_DISCOVERY_TIMEOUT,
        "lifx_rediscovery_interval": config.LIFX_REDISCOVERY_INTERVAL,
        "brightness_multiplier": config.BRIGHTNESS_MULTIPLIER,
        "govee_enabled": config.GOVEE_ENABLED,
        "govee_discovery_timeout": config.GOVEE_DISCOVERY_TIMEOUT,
        "govee_include_devices": list(config.GOVEE_INCLUDE_DEVICES),
        "color_transition_ms": config.COLOR_TRANSITION_MS,
        "include_lights": list(config.INCLUDE_LIGHTS),
        "debug_logging": config.DEBUG_LOGGING,
    }


def _apply_settings(settings: dict[str, Any]) -> None:
    port = int(settings.get("yarg_udp_port", config.YARG_UDP_PORT))
    discovery_timeout = float(
        settings.get("lifx_discovery_timeout", config.LIFX_DISCOVERY_TIMEOUT)
    )
    rediscovery_interval = float(
        settings.get(
            "lifx_rediscovery_interval", config.LIFX_REDISCOVERY_INTERVAL
        )
    )
    brightness = float(
        settings.get("brightness_multiplier", config.BRIGHTNESS_MULTIPLIER)
    )
    govee_enabled = settings.get("govee_enabled", config.GOVEE_ENABLED)
    govee_discovery_timeout = float(
        settings.get("govee_discovery_timeout", config.GOVEE_DISCOVERY_TIMEOUT)
    )
    govee_include_devices = settings.get(
        "govee_include_devices", list(config.GOVEE_INCLUDE_DEVICES)
    )
    transition = int(
        settings.get("color_transition_ms", config.COLOR_TRANSITION_MS)
    )
    include_lights = settings.get("include_lights", list(config.INCLUDE_LIGHTS))
    debug_logging = settings.get("debug_logging", config.DEBUG_LOGGING)

    if not 1 <= port <= 65535:
        raise ValueError("YARG UDP port must be between 1 and 65535.")
    if not 0.1 <= discovery_timeout <= 30:
        raise ValueError("LIFX discovery timeout must be between 0.1 and 30 seconds.")
    if not 5 <= rediscovery_interval <= 3600:
        raise ValueError("Rediscovery interval must be between 5 and 3600 seconds.")
    if not 0 <= brightness <= 1:
        raise ValueError("Brightness multiplier must be between 0 and 1.")
    if not isinstance(govee_enabled, bool):
        raise ValueError("Govee enabled setting must be true or false.")
    if not 0.1 <= govee_discovery_timeout <= 30:
        raise ValueError("Govee discovery timeout must be between 0.1 and 30 seconds.")
    if not 0 <= transition <= 60000:
        raise ValueError("Color transition must be between 0 and 60000 milliseconds.")
    if not isinstance(include_lights, list) or any(
        not isinstance(item, str) for item in include_lights
    ):
        raise ValueError("Included lights must be a list of labels or IP addresses.")
    if not isinstance(govee_include_devices, list) or any(
        not isinstance(item, str) for item in govee_include_devices
    ):
        raise ValueError("Included Govee devices must be a list of IPs, IDs, or SKUs.")
    if not isinstance(debug_logging, bool):
        raise ValueError("Debug logging setting must be true or false.")

    config.YARG_UDP_PORT = port
    config.LIFX_DISCOVERY_TIMEOUT = discovery_timeout
    config.LIFX_REDISCOVERY_INTERVAL = rediscovery_interval
    config.BRIGHTNESS_MULTIPLIER = brightness
    config.GOVEE_ENABLED = govee_enabled
    config.GOVEE_DISCOVERY_TIMEOUT = govee_discovery_timeout
    config.GOVEE_INCLUDE_DEVICES = tuple(
        item.strip() for item in govee_include_devices if item.strip()
    )
    config.COLOR_TRANSITION_MS = transition
    config.INCLUDE_LIGHTS = tuple(item.strip() for item in include_lights if item.strip())
    config.DEBUG_LOGGING = debug_logging


def _load_settings() -> None:
    if not SETTINGS_FILE.exists():
        return
    try:
        stored = json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        if not isinstance(stored, dict):
            raise ValueError("settings file must contain a JSON object")
        _apply_settings(stored)
    except (OSError, json.JSONDecodeError, TypeError, ValueError) as exc:
        logger.error("Could not load settings from %s: %s", SETTINGS_FILE, exc)


class YargLifxWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.bridge: YargBridge | None = None
        self.records: queue.Queue[str] = queue.Queue()
        self.log_handler = QueueLogHandler(self.records)
        logging.getLogger().addHandler(self.log_handler)
        self._closing = False
        self._busy = False
        self._config_widgets: list[tk.Widget] = []

        root.title("YARG-LIFX")
        root.geometry("720x600")
        root.minsize(640, 520)

        self.status = tk.StringVar(value="Stopped")
        self.port = tk.StringVar(value=str(config.YARG_UDP_PORT))
        self.discovery_timeout = tk.StringVar(
            value=str(config.LIFX_DISCOVERY_TIMEOUT)
        )
        self.rediscovery_interval = tk.StringVar(
            value=str(config.LIFX_REDISCOVERY_INTERVAL)
        )
        self.brightness = tk.StringVar(value=str(config.BRIGHTNESS_MULTIPLIER))
        self.govee_enabled = tk.BooleanVar(value=config.GOVEE_ENABLED)
        self.govee_discovery_timeout = tk.StringVar(
            value=str(config.GOVEE_DISCOVERY_TIMEOUT)
        )
        self.govee_include_devices = tk.StringVar(
            value=", ".join(config.GOVEE_INCLUDE_DEVICES)
        )
        self.transition = tk.StringVar(value=str(config.COLOR_TRANSITION_MS))
        self.include_lights = tk.StringVar(value=", ".join(config.INCLUDE_LIGHTS))
        self.debug = tk.BooleanVar(value=config.DEBUG_LOGGING)
        self.test_color = tk.StringVar(value="BLUE")

        self._build_widgets()
        self._load_log()
        root.protocol("WM_DELETE_WINDOW", self.close)
        root.after(150, self._poll)

    def _build_widgets(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        heading = ttk.Frame(main)
        heading.pack(fill=tk.X)
        ttk.Label(heading, text="YARG-LIFX", font=("", 18, "bold")).pack(
            side=tk.LEFT
        )
        ttk.Label(heading, textvariable=self.status).pack(side=tk.RIGHT)

        config_frame = ttk.LabelFrame(main, text="Configuration", padding=10)
        config_frame.pack(fill=tk.X, pady=(12, 8))
        config_frame.columnconfigure(1, weight=1)

        fields = [
            ("YARG UDP port", self.port),
            ("LIFX discovery timeout (s)", self.discovery_timeout),
            ("Rediscover lights every (s)", self.rediscovery_interval),
            ("Brightness multiplier (0-1)", self.brightness),
            ("Govee discovery timeout (s)", self.govee_discovery_timeout),
            ("Govee IPs/device IDs/SKUs (comma-separated; blank = all)", self.govee_include_devices),
            ("Color transition (ms)", self.transition),
            ("Include light labels/IPs (comma-separated; blank = all)", self.include_lights),
        ]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(config_frame, text=label).grid(
                row=row, column=0, sticky=tk.W, padx=(0, 12), pady=3
            )
            entry = ttk.Entry(config_frame, textvariable=variable)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=3)
            self._config_widgets.append(entry)

        govee_check = ttk.Checkbutton(
            config_frame, text="Enable Govee LAN lights", variable=self.govee_enabled
        )
        govee_check.grid(row=len(fields), column=0, columnspan=2, sticky=tk.W, pady=2)
        self._config_widgets.append(govee_check)
        debug_check = ttk.Checkbutton(
            config_frame, text="Show YARG lighting event logs", variable=self.debug
        )
        debug_check.grid(row=len(fields) + 1, column=0, columnspan=2, sticky=tk.W, pady=2)
        self._config_widgets.append(debug_check)

        controls = ttk.Frame(main)
        controls.pack(fill=tk.X, pady=(2, 8))
        self.start_button = ttk.Button(
            controls, text="Start listening", command=self.start
        )
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(
            controls, text="Stop", command=self.stop, state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))
        ttk.Button(controls, text="Save settings", command=self.save_settings).pack(
            side=tk.RIGHT
        )

        test_frame = ttk.LabelFrame(main, text="Test lights", padding=8)
        test_frame.pack(fill=tk.X, pady=(0, 8))
        ttk.Label(test_frame, text="Color").pack(side=tk.LEFT)
        ttk.Combobox(
            test_frame,
            textvariable=self.test_color,
            values=tuple(COLORS),
            state="readonly",
            width=12,
        ).pack(side=tk.LEFT, padx=8)
        self.test_button = ttk.Button(
            test_frame, text="Set test color", command=self.test_lights
        )
        self.test_button.pack(side=tk.LEFT)

        log_frame = ttk.LabelFrame(main, text="Activity", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(log_frame, height=12, wrap=tk.WORD, state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _load_log(self) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(
            tk.END,
            f"Settings are stored in {SETTINGS_FILE}\n"
            "Enable YARG's UDP data stream and start listening.\n",
        )
        self.log.configure(state=tk.DISABLED)

    def save_settings(self) -> bool:
        try:
            settings = {
                "yarg_udp_port": self.port.get(),
                "lifx_discovery_timeout": self.discovery_timeout.get(),
                "lifx_rediscovery_interval": self.rediscovery_interval.get(),
                "brightness_multiplier": self.brightness.get(),
                "govee_enabled": self.govee_enabled.get(),
                "govee_discovery_timeout": self.govee_discovery_timeout.get(),
                "govee_include_devices": [
                    item.strip()
                    for item in self.govee_include_devices.get().split(",")
                    if item.strip()
                ],
                "color_transition_ms": self.transition.get(),
                "include_lights": [
                    item.strip()
                    for item in self.include_lights.get().split(",")
                    if item.strip()
                ],
                "debug_logging": self.debug.get(),
            }
            _apply_settings(settings)
            SETTINGS_DIRECTORY.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(
                json.dumps(_current_settings(), indent=2) + "\n", encoding="utf-8"
            )
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self.root)
            return False
        logger.info("Settings saved")
        return True

    def start(self) -> None:
        if self._busy or (self.bridge is not None and self.bridge.running):
            return
        if not self.save_settings():
            return
        self.bridge = YargBridge()
        self.bridge.start()
        self.status.set("Starting")
        self.start_button.configure(state=tk.DISABLED)
        self.stop_button.configure(state=tk.NORMAL)
        for widget in self._config_widgets:
            widget.configure(state=tk.DISABLED)

    def stop(self) -> None:
        if self.bridge is None or not self.bridge.running or self._busy:
            return
        self.status.set("Stopping")
        self._busy = True
        threading.Thread(target=self.bridge.stop, name="bridge-stop", daemon=True).start()

    def test_lights(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.test_button.configure(state=tk.DISABLED)
        color = self.test_color.get()

        def test() -> None:
            try:
                if self.bridge is not None and self.bridge.running:
                    self.bridge.test_color(color)
                else:
                    run_test(color)
                logger.info("Test color sent: %s", color)
            except Exception:
                logger.exception("Could not apply test color")
            finally:
                self.records.put("__TEST_DONE__")

        threading.Thread(target=test, name="lifx-test", daemon=True).start()

    def _poll(self) -> None:
        self._update_logs()
        if self._closing:
            if (self.bridge is None or not self.bridge.running) and not self._busy:
                logging.getLogger().removeHandler(self.log_handler)
                self.root.destroy()
                return
        if self.bridge is None or not self.bridge.running:
            self.status.set("Stopped")
            self.start_button.configure(state=tk.NORMAL)
            self.stop_button.configure(state=tk.DISABLED)
            for widget in self._config_widgets:
                widget.configure(state=tk.NORMAL)
        elif self.bridge.ready.is_set():
            self.status.set(f"Listening on UDP {config.YARG_UDP_PORT}")
        else:
            self.status.set("Starting — discovering LIFX lights")
        if self._busy and (self.bridge is None or not self.bridge.running):
            self._busy = False
        self.root.after(150, self._poll)

    def _update_logs(self) -> None:
        while True:
            try:
                record = self.records.get_nowait()
            except queue.Empty:
                break
            if record == "__TEST_DONE__":
                self._busy = False
                self.test_button.configure(state=tk.NORMAL)
                continue
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, record + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)

    def close(self) -> None:
        self._closing = True
        if self.bridge is not None and self.bridge.running:
            self.bridge.stop(timeout=0.1)
        elif not self._busy:
            logging.getLogger().removeHandler(self.log_handler)
            self.root.destroy()


def run_gui() -> None:
    logging.basicConfig(
        level=logging.DEBUG if config.DEBUG_LOGGING else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    _load_settings()
    root = tk.Tk()
    YargLifxWindow(root)
    root.mainloop()
