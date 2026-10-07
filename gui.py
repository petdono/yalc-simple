"""Small Tkinter configuration and control window."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from typing import Any

import config
from govee import RGB_COLORS
from lifx import COLORS
from rate_limits import rate_limit_for
from runtime import LightingOutputs, YargBridge
from tuya import STATE_FILE, TuyaSetupError, connect_account, qr_matrix
from yarg import LightingIntent
from settings import (
    SETTINGS_DIRECTORY, SETTINGS_FILE, _apply_settings, _current_settings,
    _light_exclusions, _load_settings, _normalize_manual_lights,
)

logger = logging.getLogger(__name__)


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




class YargLifxWindow:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.bridge: YargBridge | None = None
        self.records: queue.Queue[str] = queue.Queue()
        self._ui_callbacks: queue.Queue[Any] = queue.Queue()
        self.log_handler = QueueLogHandler(self.records)
        logging.getLogger().addHandler(self.log_handler)
        self._closing = False
        self._busy = False
        self._stop_in_progress = False
        self._config_widgets: list[tk.Widget] = []
        self._settings_window: tk.Toplevel | None = None
        self._manage_window: tk.Toplevel | None = None
        self._devices_window: tk.Toplevel | None = None
        self._tuya_login_window: tk.Toplevel | None = None
        self._tuya_login_cancel: threading.Event | None = None
        self._tuya_qr_image: tk.PhotoImage | None = None
        self._rate_test_window: tk.Toplevel | None = None
        self._rate_test_stop = threading.Event()
        self._light_rows: list[tuple[str, str, str]] = []
        self._light_row_frame: ttk.Frame | None = None
        self._excluded_lights = set(config.EXCLUDE_LIGHTS)
        self._manual_lights = list(config.MANUAL_LIGHTS)
        self.outputs = LightingOutputs()
        self._outputs_ready = False
        self._outputs_starting = False
        self._outputs_closed = False
        self._scan_in_progress = False
        self._start_after_scan = False
        self._scan_button: ttk.Button | None = None

        root.title("YALC Simplified")
        root.geometry("600x480")
        root.minsize(540, 420)

        self.status = tk.StringVar(value="Stopped")
        self.port = tk.StringVar(value=str(config.YARG_UDP_PORT))
        self.discovery_timeout = tk.StringVar(
            value=str(config.LIFX_DISCOVERY_TIMEOUT)
        )
        self.brightness = tk.StringVar(value=str(config.BRIGHTNESS_MULTIPLIER))
        self.lifx_enabled = tk.BooleanVar(value=config.LIFX_ENABLED)
        self.govee_enabled = tk.BooleanVar(value=config.GOVEE_ENABLED)
        self.tuya_enabled = tk.BooleanVar(value=config.TUYA_ENABLED)
        self.tuya_rate = tk.StringVar(
            value=str(config.TUYA_MAX_UPDATES_PER_SECOND)
        )
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
        root.after(100, self._initialize_outputs)
        root.after(150, self._poll)

    def _build_widgets(self) -> None:
        main = ttk.Frame(self.root, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        heading = ttk.Frame(main)
        heading.pack(fill=tk.X)
        ttk.Label(heading, text="YALC Simplified", font=("", 18, "bold")).pack(side=tk.LEFT)
        ttk.Label(heading, textvariable=self.status).pack(side=tk.RIGHT)

        navigation = ttk.Frame(main)
        navigation.pack(fill=tk.X, pady=(16, 10))
        ttk.Button(navigation, text="Settings", command=self.open_settings).pack(
            side=tk.LEFT, expand=True, fill=tk.X
        )
        ttk.Button(navigation, text="Manage Lights", command=self.open_manage_lights).pack(
            side=tk.LEFT, expand=True, fill=tk.X, padx=8
        )
        ttk.Button(navigation, text="Devices", command=self.open_devices).pack(
            side=tk.LEFT, expand=True, fill=tk.X
        )

        controls = ttk.Frame(main)
        controls.pack(fill=tk.X, pady=(2, 10))
        self.start_button = ttk.Button(
            controls, text="Start listening", command=self.start
        )
        self.start_button.pack(side=tk.LEFT)
        self.stop_button = ttk.Button(
            controls, text="Stop", command=self.stop, state=tk.DISABLED
        )
        self.stop_button.pack(side=tk.LEFT, padx=(8, 0))

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
        ttk.Button(
            test_frame,
            text="Rate limit tester",
            command=self.open_rate_limit_tester,
        ).pack(side=tk.LEFT, padx=(8, 0))

        log_frame = ttk.LabelFrame(main, text="Activity", padding=6)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(log_frame, height=12, wrap=tk.WORD, state=tk.DISABLED)
        scrollbar = ttk.Scrollbar(log_frame, command=self.log.yview)
        self.log.configure(yscrollcommand=scrollbar.set)
        self.log.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

    def _schedule_ui(self, callback: Any) -> None:
        """Workers enqueue results; only the Tk main thread touches widgets."""
        self._ui_callbacks.put(callback)

    def open_settings(self) -> None:
        if self._settings_window is not None and self._settings_window.winfo_exists():
            self._settings_window.lift()
            return
        window = tk.Toplevel(self.root)
        self._settings_window = window
        window.title("Settings")
        window.geometry("620x500")
        window.transient(self.root)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        frame.columnconfigure(1, weight=1)
        fields = [
            ("YARG UDP port", self.port),
            ("LIFX discovery timeout (s)", self.discovery_timeout),
            ("Brightness multiplier (0-1)", self.brightness),
            ("Govee discovery timeout (s)", self.govee_discovery_timeout),
            ("Govee IPs/device IDs/SKUs (comma-separated; blank = all)", self.govee_include_devices),
            ("Tuya maximum updates per second (1-50)", self.tuya_rate),
            ("Color transition (ms)", self.transition),
            ("Include light labels/IPs (comma-separated; blank = all)", self.include_lights),
        ]
        for row, (label, variable) in enumerate(fields):
            ttk.Label(frame, text=label).grid(
                row=row, column=0, sticky=tk.W, padx=(0, 12), pady=4
            )
            entry = ttk.Entry(frame, textvariable=variable)
            entry.grid(row=row, column=1, sticky=tk.EW, pady=4)
            self._config_widgets.append(entry)
        debug_check = ttk.Checkbutton(
            frame, text="Show YARG lighting event logs", variable=self.debug
        )
        debug_check.grid(row=len(fields), column=0, columnspan=2, sticky=tk.W, pady=6)
        self._config_widgets.append(debug_check)
        ttk.Button(frame, text="Save settings", command=self.save_settings).grid(
            row=len(fields) + 1, column=1, sticky=tk.E, pady=(12, 0)
        )
        window.protocol(
            "WM_DELETE_WINDOW",
            lambda: self._close_popup(window, "_settings_window"),
        )

    def open_manage_lights(self) -> None:
        if self._manage_window is not None and self._manage_window.winfo_exists():
            self._manage_window.lift()
            return
        window = tk.Toplevel(self.root)
        self._manage_window = window
        window.title("Manage Lights")
        window.geometry("820x480")
        window.transient(self.root)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        ttk.Label(
            frame,
            text=(
                "Enable lights, identify one, or set its update rate. Blank uses "
                "the Tuya default; LIFX/Govee are unlimited unless set here."
            ),
            wraplength=560,
        ).pack(anchor=tk.W, pady=(0, 8))
        list_frame = ttk.Frame(frame)
        list_frame.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(list_frame, highlightthickness=0)
        scrollbar = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._light_row_frame = ttk.Frame(canvas)
        canvas_window = canvas.create_window(
            (0, 0), window=self._light_row_frame, anchor=tk.NW
        )
        self._light_row_frame.bind(
            "<Configure>",
            lambda event: canvas.configure(
                scrollregion=(0, 0, event.width, event.height)
            ),
        )
        canvas.bind(
            "<Configure>",
            lambda event: canvas.itemconfigure(canvas_window, width=event.width),
        )
        actions = ttk.Frame(frame)
        actions.pack(fill=tk.X, pady=(8, 0))
        self._scan_button = ttk.Button(
            actions, text="Scan lights", command=self.scan_lights
        )
        self._scan_button.pack(side=tk.LEFT)
        window.protocol(
            "WM_DELETE_WINDOW", lambda: self._close_popup(window, "_manage_window")
        )
        self._show_lights(self.outputs.all_discovered_devices())

    def open_devices(self) -> None:
        if self._devices_window is not None and self._devices_window.winfo_exists():
            self._devices_window.lift()
            return
        window = tk.Toplevel(self.root)
        self._devices_window = window
        window.title("Devices")
        window.geometry("640x580")
        window.transient(self.root)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        providers = ttk.LabelFrame(frame, text="Providers", padding=8)
        providers.pack(fill=tk.X)
        self._provider_widgets = []
        for name, variable in (
            ("Enable LIFX", self.lifx_enabled),
            ("Enable Govee", self.govee_enabled),
            ("Enable Smart Life / Tuya", self.tuya_enabled),
        ):
            checkbox = ttk.Checkbutton(providers, text=name, variable=variable)
            checkbox.pack(side=tk.LEFT, padx=(0, 16))
            self._provider_widgets.append(checkbox)
            self._config_widgets.append(checkbox)

        tuya_account = ttk.LabelFrame(
            frame, text="Smart Life / Tuya account", padding=8
        )
        tuya_account.pack(fill=tk.X, pady=(10, 0))
        self.tuya_account_status = tk.StringVar(
            value=(
                "Local device credentials found."
                if STATE_FILE.exists()
                else "No account linked. Link once; normal control stays on your LAN."
            )
        )
        ttk.Label(
            tuya_account,
            textvariable=self.tuya_account_status,
            wraplength=580,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._tuya_connect_button = ttk.Button(
            tuya_account,
            text="Connect / refresh",
            command=self.connect_tuya_account,
        )
        self._tuya_connect_button.pack(side=tk.RIGHT, padx=(8, 0))
        self._config_widgets.append(self._tuya_connect_button)

        manual = ttk.LabelFrame(frame, text="Manually added lights", padding=8)
        manual.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.manual_list = tk.Listbox(manual, height=9, exportselection=False)
        self.manual_list.pack(fill=tk.BOTH, expand=True)
        self._config_widgets.append(self.manual_list)
        self._render_manual_lights()
        form = ttk.Frame(manual)
        form.pack(fill=tk.X, pady=(8, 0))
        self.manual_provider = tk.StringVar(value="LIFX")
        self.manual_name = tk.StringVar()
        self.manual_ip = tk.StringVar()
        self.manual_mac = tk.StringVar()
        ttk.Label(form, text="Provider").grid(row=0, column=0, sticky=tk.W)
        provider_box = ttk.Combobox(
            form,
            textvariable=self.manual_provider,
            values=("LIFX", "Govee"),
            state="readonly",
            width=10,
        )
        provider_box.grid(row=1, column=0, sticky=tk.EW, padx=(0, 6))
        ttk.Label(form, text="Name").grid(row=0, column=1, sticky=tk.W)
        name_entry = ttk.Entry(form, textvariable=self.manual_name)
        name_entry.grid(row=1, column=1, sticky=tk.EW, padx=6)
        ttk.Label(form, text="IP address").grid(row=0, column=2, sticky=tk.W)
        ip_entry = ttk.Entry(form, textvariable=self.manual_ip)
        ip_entry.grid(row=1, column=2, sticky=tk.EW, padx=6)
        ttk.Label(form, text="LIFX MAC").grid(row=0, column=3, sticky=tk.W)
        mac_entry = ttk.Entry(form, textvariable=self.manual_mac)
        mac_entry.grid(row=1, column=3, sticky=tk.EW)
        form.columnconfigure(1, weight=1)
        form.columnconfigure(2, weight=1)
        add_button = ttk.Button(form, text="Add", command=self.add_manual_light)
        add_button.grid(row=1, column=4, padx=(8, 0))
        actions = ttk.Frame(manual)
        actions.pack(fill=tk.X, pady=(8, 0))
        remove_button = ttk.Button(
            actions, text="Remove selected", command=self.remove_manual_light
        )
        remove_button.pack(side=tk.LEFT)
        save_button = ttk.Button(actions, text="Save devices", command=self.save_devices)
        save_button.pack(side=tk.RIGHT)
        self._config_widgets.extend(
            [
                provider_box,
                name_entry,
                ip_entry,
                mac_entry,
                add_button,
                remove_button,
                save_button,
            ]
        )
        window.protocol(
            "WM_DELETE_WINDOW", lambda: self._close_popup(window, "_devices_window")
        )

    def connect_tuya_account(self) -> None:
        if self._tuya_login_window is not None and self._tuya_login_window.winfo_exists():
            self._tuya_login_window.lift()
            return
        user_code = simpledialog.askstring(
            "Smart Life user code",
            "In Smart Life, open Me > Settings > Account and Security > User Code.",
            parent=self._devices_window or self.root,
        )
        if not user_code or not user_code.strip():
            return

        window = tk.Toplevel(self.root)
        self._tuya_login_window = window
        self._tuya_login_cancel = threading.Event()
        self._tuya_qr_image = None
        window.title("Connect Smart Life / Tuya")
        window.geometry("390x440")
        window.transient(self.root)
        frame = ttk.Frame(window, padding=12)
        frame.pack(fill=tk.BOTH, expand=True)
        status = tk.StringVar(value="Starting secure QR login...")
        ttk.Label(
            frame,
            text="Scan with the Smart Life app (+ > Scan), then approve the request.",
            wraplength=360,
            justify=tk.CENTER,
        ).pack(fill=tk.X, pady=(0, 8))
        qr_label = ttk.Label(frame)
        qr_label.pack(expand=True)
        ttk.Label(frame, textvariable=status, wraplength=360).pack(
            fill=tk.X, pady=(8, 0)
        )

        def close_login() -> None:
            if self._tuya_login_cancel is not None:
                self._tuya_login_cancel.set()
            window.destroy()
            self._tuya_login_window = None
            self._tuya_qr_image = None

        ttk.Button(frame, text="Cancel", command=close_login).pack(
            anchor=tk.E, pady=(8, 0)
        )
        window.protocol("WM_DELETE_WINDOW", close_login)
        cancel = self._tuya_login_cancel

        def schedule(callback: Any) -> None:
            if not self._closing:
                self._schedule_ui(callback)

        def show_qr(payload: str) -> None:
            if not window.winfo_exists():
                return
            try:
                matrix = qr_matrix(payload)
                size = len(matrix)
                image = tk.PhotoImage(master=self.root, width=size, height=size)
                for y, row in enumerate(matrix):
                    pixels = " ".join(
                        "#000000" if cell else "#ffffff" for cell in row
                    )
                    image.put("{" + pixels + "}", to=(0, y))
                self._tuya_qr_image = image.zoom(4, 4)
                qr_label.configure(image=self._tuya_qr_image)
            except tk.TclError:
                status.set("Could not render the QR code. Use `python main.py --tuya-login`.")
                logger.exception("Could not render Smart Life QR code")

        def report_status(message: str) -> None:
            if window.winfo_exists():
                status.set(message)
                self.tuya_account_status.set(message)

        def run_login() -> None:
            try:
                names = connect_account(
                    user_code.strip(),
                    on_qr=lambda payload: schedule(
                        lambda qr=payload: show_qr(qr)
                    ),
                    on_status=lambda message: schedule(
                        lambda text=message: report_status(text)
                    ),
                    cancel=cancel,
                )
            except TuyaSetupError as exc:
                error = str(exc)
                schedule(lambda: self._tuya_login_finished(None, error))
            except Exception as exc:
                logger.error("Unexpected Smart Life setup error (%s)", type(exc).__name__)
                error = f"Unexpected setup error ({type(exc).__name__})."
                schedule(lambda: self._tuya_login_finished(None, error))
            else:
                schedule(lambda: self._tuya_login_finished(names, None))

        threading.Thread(target=run_login, name="tuya-account-link", daemon=True).start()

    def _tuya_login_finished(
        self, names: list[str] | None, error: str | None
    ) -> None:
        window = self._tuya_login_window
        if window is None or not window.winfo_exists():
            return
        if error is not None:
            self.tuya_account_status.set(error)
            messagebox.showerror("Smart Life setup failed", error, parent=window)
            return

        self.tuya_enabled.set(True)
        if not self.save_devices(force_recreate=True):
            self.tuya_account_status.set("Credentials retrieved; settings could not be saved.")
            return
        count = len(names or [])
        summary = (
            f"Account linked. Retrieved local credentials for {count} compatible "
            "light(s). LAN reachability is checked during discovery."
        )
        self.tuya_account_status.set(summary)
        messagebox.showinfo("Smart Life connected", summary, parent=window)
        window.destroy()
        self._tuya_login_window = None
        self._tuya_qr_image = None

    def _close_popup(self, window: tk.Toplevel, attribute: str) -> None:
        self._config_widgets = [
            widget
            for widget in self._config_widgets
            if widget.winfo_toplevel() is not window
        ]
        window.destroy()
        setattr(self, attribute, None)

    def _persist_settings(self) -> None:
        SETTINGS_DIRECTORY.mkdir(parents=True, exist_ok=True)
        SETTINGS_FILE.write_text(
            json.dumps(_current_settings(), indent=2) + "\n", encoding="utf-8"
        )

    def scan_lights(self) -> None:
        if self._scan_in_progress or self._outputs_starting:
            return
        if not self._outputs_ready:
            self._initialize_outputs()
            return
        self._scan_in_progress = True
        if self._scan_button is not None:
            self._scan_button.configure(state=tk.DISABLED, text="Scanning...")

        def scan() -> None:
            try:
                self.outputs.scan()
                devices = self.outputs.all_discovered_devices()
                self._schedule_ui(lambda: self._scan_finished(devices))
            except Exception as exc:
                logger.exception("Could not scan for lights")
                self._schedule_ui(
                    lambda error=str(exc): self._scan_failed(error),
                )

        threading.Thread(target=scan, name="light-scan", daemon=True).start()

    def _scan_finished(self, devices: list[tuple[str, str, str]]) -> None:
        self._scan_in_progress = False
        if self._scan_button is not None and self._scan_button.winfo_exists():
            self._scan_button.configure(state=tk.NORMAL, text="Scan lights")
        self._show_lights(devices)

    def _scan_failed(self, error: str) -> None:
        self._scan_in_progress = False
        if self._scan_button is not None and self._scan_button.winfo_exists():
            self._scan_button.configure(state=tk.NORMAL, text="Scan lights")
        messagebox.showerror("Light scan failed", error, parent=self.root)

    def _initialize_outputs(self, recreate: bool = False) -> None:
        if self._outputs_starting:
            return
        self._outputs_starting = True
        self._outputs_ready = False
        self.status.set("Scanning lights...")

        def initialize() -> None:
            try:
                if recreate:
                    self.outputs.close()
                    self.outputs = LightingOutputs()
                self.outputs.start()
                devices = self.outputs.discovered_devices()
                self._schedule_ui(lambda: self._outputs_initialized(devices, None))
            except Exception as exc:
                logger.exception("Could not initialize lighting outputs")
                self._schedule_ui(
                    lambda error=str(exc): self._outputs_initialized([], error),
                )

        threading.Thread(target=initialize, name="light-discovery", daemon=True).start()

    def _outputs_initialized(
        self, devices: list[tuple[str, str, str]], error: str | None
    ) -> None:
        self._outputs_starting = False
        self._outputs_ready = error is None
        self.status.set("Stopped" if error is None else "Scan failed")
        self._show_lights(devices)
        if error is not None:
            messagebox.showerror("Light scan failed", error, parent=self.root)
        if self._start_after_scan and self._outputs_ready:
            self._start_after_scan = False
            self._launch_bridge()

    def _show_lights(self, devices: list[tuple[str, str, str]]) -> None:
        self._light_rows = devices
        if self._manage_window is None or not self._manage_window.winfo_exists():
            return
        if self._light_row_frame is None:
            return
        for child in self._light_row_frame.winfo_children():
            child.destroy()
        self._config_widgets = [w for w in self._config_widgets if w.winfo_exists()]
        excluded_ips = {value.casefold() for value in self._excluded_lights}
        for row, (provider, label, ip_address) in enumerate(devices):
            enabled = tk.BooleanVar(value=ip_address.casefold() not in excluded_ips)
            check = ttk.Checkbutton(
                self._light_row_frame,
                text=f"{provider} | {label} | {ip_address}",
                variable=enabled,
                command=lambda ip=ip_address, value=enabled: self._set_light_enabled(
                    ip, value.get()
                ),
            )
            check.grid(row=row, column=0, sticky=tk.W, padx=(4, 12), pady=3)
            rate_key = self.outputs.rate_limit_key(provider, ip_address)
            rate_value = tk.StringVar(
                value=(
                    str(config.LIGHT_RATE_LIMITS[rate_key])
                    if rate_key in config.LIGHT_RATE_LIMITS
                    else ""
                )
            )
            ttk.Label(self._light_row_frame, text="Updates/sec").grid(
                row=row, column=1, sticky=tk.E, padx=(4, 2), pady=3
            )
            rate_entry = ttk.Entry(
                self._light_row_frame, textvariable=rate_value, width=6
            )
            rate_entry.grid(row=row, column=2, sticky=tk.E, padx=2, pady=3)
            save_rate = ttk.Button(
                self._light_row_frame,
                text="Save rate",
                command=lambda key=rate_key, brand=provider, value=rate_value: self._save_light_rate_limit(
                    key, brand, value, self._manage_window
                ),
            )
            save_rate.grid(row=row, column=3, sticky=tk.E, padx=4, pady=3)
            self._config_widgets.extend([rate_entry, save_rate])
            ttk.Button(
                self._light_row_frame,
                text="Identify",
                command=lambda brand=provider, ip=ip_address: self.identify_light(
                    brand, ip
                ),
            ).grid(row=row, column=4, sticky=tk.E, padx=4, pady=3)
        if not devices:
            ttk.Label(self._light_row_frame, text="No lights found.").grid(
                row=0, column=0, sticky=tk.W, padx=4, pady=3
            )

    def _set_light_enabled(self, ip_address: str, enabled: bool) -> None:
        previous_exclusions = self._excluded_lights
        self._excluded_lights = _light_exclusions(
            self._excluded_lights, ip_address, enabled
        )
        config.EXCLUDE_LIGHTS = tuple(sorted(self._excluded_lights, key=str.casefold))
        try:
            self._persist_settings()
        except OSError as exc:
            self._excluded_lights = previous_exclusions
            config.EXCLUDE_LIGHTS = tuple(
                sorted(previous_exclusions, key=str.casefold)
            )
            logger.exception("Could not save excluded lights")
            messagebox.showerror("Could not save lights", str(exc), parent=self.root)
            self._show_lights(self._light_rows)
            return
        if self._outputs_ready:
            self.outputs.apply_filters()
            self._show_lights(self.outputs.all_discovered_devices())
        logger.info(
            "%s light %s",
            "Enabled" if enabled else "Excluded",
            ip_address,
        )

    def identify_light(self, provider: str, ip_address: str) -> None:
        if not self._outputs_ready:
            return

        def identify() -> None:
            try:
                self.outputs.identify(provider, ip_address)
                logger.info("Identified %s light at %s", provider, ip_address)
            except Exception as exc:
                logger.exception("Could not identify %s light at %s", provider, ip_address)
                self._schedule_ui(
                    lambda error=str(exc): messagebox.showerror(
                        "Could not identify light", error, parent=self.root
                    ),
                )

        threading.Thread(target=identify, name="identify-light", daemon=True).start()

    def _render_manual_lights(self) -> None:
        self.manual_list.delete(0, tk.END)
        for provider, name, ip_address, mac in self._manual_lights:
            suffix = f" | {mac}" if mac else ""
            self.manual_list.insert(
                tk.END, f"{provider.upper()} | {name} | {ip_address}{suffix}"
            )

    def add_manual_light(self) -> None:
        provider = self.manual_provider.get().lower()
        candidate = list(self._manual_lights) + [
            (
                provider,
                self.manual_name.get(),
                self.manual_ip.get(),
                self.manual_mac.get(),
            )
        ]
        try:
            self._manual_lights = list(
                _normalize_manual_lights(
                    [
                        {"provider": p, "name": name, "ip": ip, "mac": mac}
                        for p, name, ip, mac in candidate
                    ]
                )
            )
        except ValueError as exc:
            messagebox.showerror("Invalid device", str(exc), parent=self._devices_window)
            return
        self._render_manual_lights()
        self.manual_name.set("")
        self.manual_ip.set("")
        self.manual_mac.set("")

    def remove_manual_light(self) -> None:
        selection = self.manual_list.curselection()
        if selection:
            del self._manual_lights[selection[0]]
            self._render_manual_lights()

    def save_devices(self, force_recreate: bool = False) -> bool:
        previous_devices = (
            config.LIFX_ENABLED,
            config.GOVEE_ENABLED,
            config.TUYA_ENABLED,
            config.MANUAL_LIGHTS,
        )
        settings = _current_settings()
        settings.update(
            {
                "lifx_enabled": self.lifx_enabled.get(),
                "govee_enabled": self.govee_enabled.get(),
                "tuya_enabled": self.tuya_enabled.get(),
                "tuya_max_updates_per_second": self.tuya_rate.get(),
                "manual_lights": [
                    {"provider": provider, "name": name, "ip": ip, "mac": mac}
                    for provider, name, ip, mac in self._manual_lights
                ],
            }
        )
        try:
            _apply_settings(settings)
            self._persist_settings()
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("Invalid devices", str(exc), parent=self._devices_window)
            return False
        if previous_devices != (
            config.LIFX_ENABLED,
            config.GOVEE_ENABLED,
            config.TUYA_ENABLED,
            config.MANUAL_LIGHTS,
        ) or force_recreate:
            self._initialize_outputs(recreate=True)
        logger.info("Device settings saved")
        return True

    def _load_log(self) -> None:
        self.log.configure(state=tk.NORMAL)
        self.log.insert(
            tk.END,
            f"Settings are stored in {SETTINGS_FILE}\n"
            f"Smart Life credentials are stored separately in {STATE_FILE}\n"
            "Use Devices > Connect / refresh to link Smart Life once.\n"
            "Enable YARG's UDP data stream and start listening.\n",
        )
        self.log.configure(state=tk.DISABLED)

    def save_settings(self) -> bool:
        previous_scan_settings = (
            config.LIFX_DISCOVERY_TIMEOUT,
            config.GOVEE_DISCOVERY_TIMEOUT,
            config.GOVEE_INCLUDE_DEVICES,
            config.INCLUDE_LIGHTS,
            config.EXCLUDE_LIGHTS,
        )
        try:
            settings = {
                "yarg_udp_port": self.port.get(),
                "lifx_discovery_timeout": self.discovery_timeout.get(),
                "brightness_multiplier": self.brightness.get(),
                "govee_discovery_timeout": self.govee_discovery_timeout.get(),
                "govee_include_devices": [
                    item.strip()
                    for item in self.govee_include_devices.get().split(",")
                    if item.strip()
                ],
                "tuya_max_updates_per_second": self.tuya_rate.get(),
                "color_transition_ms": self.transition.get(),
                "include_lights": [
                    item.strip()
                    for item in self.include_lights.get().split(",")
                    if item.strip()
                ],
                "debug_logging": self.debug.get(),
            }
            _apply_settings(settings)
            self._persist_settings()
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("Invalid settings", str(exc), parent=self.root)
            return False
        if previous_scan_settings != (
            config.LIFX_DISCOVERY_TIMEOUT,
            config.GOVEE_DISCOVERY_TIMEOUT,
            config.GOVEE_INCLUDE_DEVICES,
            config.INCLUDE_LIGHTS,
            config.EXCLUDE_LIGHTS,
        ):
            self._initialize_outputs(recreate=True)
        logger.info("Settings saved")
        return True

    def start(self) -> None:
        if self._busy or (self.bridge is not None and self.bridge.running):
            return
        if not self.save_settings():
            return
        if not self._outputs_ready:
            self._start_after_scan = True
            if not self._outputs_starting:
                self._initialize_outputs()
            self.status.set("Scanning lights before starting...")
            self.start_button.configure(state=tk.DISABLED)
            return
        self._launch_bridge()

    def _launch_bridge(self) -> None:
        self.bridge = YargBridge(self.outputs)
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
        self._stop_in_progress = True
        threading.Thread(target=self.bridge.stop, name="bridge-stop", daemon=True).start()

    def test_lights(self) -> None:
        if self._busy:
            return
        self._busy = True
        self.test_button.configure(state=tk.DISABLED)
        color = self.test_color.get()

        def test() -> None:
            try:
                self.outputs.submit(LightingIntent(color, transition_ms=250))
                if not self.outputs.wait_until_applied(10.0):
                    raise TimeoutError(
                        f"Timed out waiting for the {color.lower()} test color"
                    )
                logger.info("Test color sent: %s", color)
            except Exception:
                logger.exception("Could not apply test color")
            finally:
                self.records.put("__TEST_DONE__")

        threading.Thread(target=test, name="lifx-test", daemon=True).start()

    def open_rate_limit_tester(self) -> None:
        if self._rate_test_window is not None and self._rate_test_window.winfo_exists():
            self._rate_test_window.lift()
            return
        if not self._outputs_ready:
            messagebox.showinfo(
                "Lights are still scanning",
                "Wait for light discovery to finish, then open the tester.",
                parent=self.root,
            )
            return

        window = tk.Toplevel(self.root)
        self._rate_test_window = window
        window.title("Light rate limit tester")
        window.geometry("430x260")
        window.resizable(False, False)
        window.transient(self.root)
        frame = ttk.Frame(window, padding=16)
        frame.pack(fill=tk.BOTH, expand=True)

        devices = self.outputs.all_discovered_devices()
        device_labels: list[str] = []
        device_by_label: dict[str, tuple[str, str]] = {}
        for provider, name, ip_address in devices:
            label = f"{provider} | {name} ({ip_address})"
            device_labels.append(label)
            device_by_label[label] = (provider, ip_address)
        selected_device = tk.StringVar(
            value=device_labels[0] if device_labels else ""
        )
        ttk.Label(frame, text="Light").grid(row=0, column=0, sticky=tk.W, pady=6)
        light_picker = ttk.Combobox(
            frame,
            textvariable=selected_device,
            values=device_labels,
            state="readonly" if device_labels else "disabled",
            width=43,
        )
        light_picker.grid(row=0, column=1, sticky=tk.EW, pady=6)

        ttk.Label(frame, text="Updates/sec (blank = provider default)").grid(
            row=1, column=0, sticky=tk.W, pady=6
        )
        test_rate = tk.StringVar()
        rate_picker = ttk.Spinbox(
            frame,
            from_=1,
            to=50,
            increment=1,
            textvariable=test_rate,
            width=8,
        )
        rate_picker.grid(row=1, column=1, sticky=tk.W, pady=6)

        color_preview = tk.Label(
            frame,
            text="Ready",
            width=26,
            height=3,
            relief=tk.GROOVE,
            background="#eeeeee",
        )
        color_preview.grid(row=2, column=0, columnspan=2, sticky=tk.EW, pady=12)
        status = tk.StringVar(
            value=(
                "Select a light and start the color sequence."
                if device_labels
                else "No discovered lights are available."
            )
        )
        ttk.Label(frame, textvariable=status, wraplength=390).grid(
            row=3, column=0, columnspan=2, sticky=tk.W
        )
        actions = ttk.Frame(frame)
        actions.grid(row=4, column=0, columnspan=2, sticky=tk.E, pady=(12, 0))
        start_button = ttk.Button(actions, text="Start test")
        start_button.pack(side=tk.LEFT)
        save_rate_button = ttk.Button(
            actions,
            text="Save light rate",
            command=lambda: self._save_tester_rate(
                selected_device.get(),
                device_by_label,
                test_rate,
                window,
            ),
        )
        save_rate_button.pack(side=tk.LEFT, padx=(8, 0))
        stop_button = ttk.Button(actions, text="Stop", state=tk.DISABLED)
        stop_button.pack(side=tk.LEFT, padx=(8, 0))
        frame.columnconfigure(1, weight=1)

        def schedule(callback: Any) -> None:
            def apply_if_open() -> None:
                if not self._closing and window.winfo_exists():
                    callback()
            self._schedule_ui(apply_if_open)

        def stop_test() -> None:
            self._rate_test_stop.set()
            status.set("Stopping test...")
            stop_button.configure(state=tk.DISABLED)

        def load_selected_rate(_event: Any = None) -> None:
            _ = _event
            device = device_by_label.get(selected_device.get())
            is_tuya = device is not None and device[0].casefold() == "tuya"
            light_picker.configure(state="readonly" if device_labels else "disabled")
            rate_picker.configure(state="normal" if device is not None else "disabled")
            save_rate_button.configure(state="normal" if device is not None else "disabled")
            if device is not None:
                device_key = self.outputs.rate_limit_key(device[0], device[1])
                test_rate.set(
                    str(config.LIGHT_RATE_LIMITS[device_key])
                    if device_key in config.LIGHT_RATE_LIMITS
                    else ""
                )
                if is_tuya:
                    status.set(
                        f"Blank uses the Tuya default ({config.TUYA_MAX_UPDATES_PER_SECOND} updates/sec)."
                    )
                else:
                    status.set("Blank means no output rate limit for LIFX/Govee.")
            else:
                test_rate.set("")
                status.set("No discovered lights are available.")

        light_picker.bind("<<ComboboxSelected>>", load_selected_rate)
        load_selected_rate()

        def start_test() -> None:
            if self.bridge is not None and self.bridge.running:
                messagebox.showwarning(
                    "Stop YARG listening first",
                    "Stop YARG listening before running a light rate test.",
                    parent=window,
                )
                return
            if self._busy or not device_by_label:
                return
            provider, selector = device_by_label[selected_device.get()]
            if not self._save_tester_rate(
                selected_device.get(), device_by_label, test_rate, window
            ):
                return
            rate_key = self.outputs.rate_limit_key(provider, selector)
            updates_per_second = rate_limit_for(
                provider, rate_key.split(":", 1)[1]
            )
            rate_interval = (
                1.0 / updates_per_second if updates_per_second is not None else 1.0
            )
            self._rate_test_stop.clear()
            self._busy = True
            start_button.configure(state=tk.DISABLED)
            save_rate_button.configure(state=tk.DISABLED)
            rate_picker.configure(state=tk.DISABLED)
            stop_button.configure(state=tk.NORMAL)
            light_picker.configure(state=tk.DISABLED)
            colors = tuple(COLORS)

            def run_sequence() -> None:
                next_update = time.monotonic()
                index = 0
                try:
                    while not self._rate_test_stop.is_set():
                        color = colors[index % len(colors)]
                        hex_color = "#{:02x}{:02x}{:02x}".format(
                            *RGB_COLORS[color]
                        )
                        schedule(
                            lambda name=color, swatch=hex_color: (
                                color_preview.configure(
                                    text=f"Sending {name}", background=swatch
                                ),
                                status.set(
                                    (
                                        f"Target: {updates_per_second} updates/sec"
                                        if updates_per_second is not None
                                        else "No limit configured; tester preview runs at 1 color/sec."
                                    )
                                ),
                            )
                        )
                        self.outputs.test_light_color(provider, selector, color)
                        index += 1
                        next_update += rate_interval
                        if next_update < time.monotonic():
                            next_update = time.monotonic()
                        self._rate_test_stop.wait(
                            max(0.0, next_update - time.monotonic())
                        )
                except Exception as exc:
                    logger.exception("Light rate-limit test failed")
                    schedule(
                        lambda error=str(exc): messagebox.showerror(
                            "Rate-limit test failed", error, parent=window
                        )
                    )
                finally:
                    self.records.put("__RATE_TEST_DONE__")
                    schedule(
                        lambda: (
                            status.set("Test stopped."),
                            start_button.configure(state=tk.NORMAL),
                            save_rate_button.configure(state=tk.NORMAL),
                            rate_picker.configure(state=tk.NORMAL),
                            stop_button.configure(state=tk.DISABLED),
                            light_picker.configure(
                                state="readonly" if device_labels else "disabled"
                            ),
                            load_selected_rate(),
                        )
                    )

            threading.Thread(
                target=run_sequence, name="light-rate-test", daemon=True
            ).start()

        start_button.configure(command=start_test)
        stop_button.configure(command=stop_test)

        def close_tester() -> None:
            self._rate_test_stop.set()
            window.destroy()
            self._rate_test_window = None

        window.protocol("WM_DELETE_WINDOW", close_tester)

    def _save_tester_rate(
        self,
        selection: str,
        devices: dict[str, tuple[str, str]],
        value: tk.StringVar,
        parent: tk.Misc,
    ) -> bool:
        device = devices.get(selection)
        if device is None:
            messagebox.showerror(
                "Light is no longer available",
                "Select a discovered light before saving its rate limit.",
                parent=parent,
            )
            return False
        try:
            device_key = self.outputs.rate_limit_key(device[0], device[1])
        except LookupError as exc:
            messagebox.showerror("Light is no longer available", str(exc), parent=parent)
            return False
        return self._save_light_rate_limit(device_key, device[0], value, parent)

    def _save_tuya_rate(self, parent: tk.Misc) -> bool:
        try:
            _apply_settings(
                {"tuya_max_updates_per_second": self.tuya_rate.get()}
            )
            self._persist_settings()
        except (OSError, TypeError, ValueError) as exc:
            messagebox.showerror("Invalid rate limit", str(exc), parent=parent)
            return False
        self.tuya_rate.set(str(config.TUYA_MAX_UPDATES_PER_SECOND))
        logger.info(
            "Tuya light rate limit set to %d updates per second",
            config.TUYA_MAX_UPDATES_PER_SECOND,
        )
        return True

    def _save_light_rate_limit(
        self,
        device_key: str,
        provider: str,
        value: tk.StringVar,
        parent: tk.Misc | None,
    ) -> bool:
        previous_limits = dict(config.LIGHT_RATE_LIMITS)
        limits = dict(previous_limits)
        entered = value.get().strip()
        if entered:
            try:
                rate = int(entered)
            except ValueError:
                messagebox.showerror(
                    "Invalid rate limit",
                    "Enter a whole number from 1 to 50, or leave it blank to use the provider default.",
                    parent=parent,
                )
                return False
            if not 1 <= rate <= 50:
                messagebox.showerror(
                    "Invalid rate limit",
                    "Per-light rate limits must be between 1 and 50.",
                    parent=parent,
                )
                return False
            limits[device_key] = rate
        else:
            limits.pop(device_key, None)
        try:
            _apply_settings({"light_rate_limits": limits})
            self._persist_settings()
        except (OSError, TypeError, ValueError) as exc:
            config.LIGHT_RATE_LIMITS = previous_limits
            messagebox.showerror("Could not save rate limit", str(exc), parent=parent)
            return False
        if entered:
            value.set(str(limits[device_key]))
            logger.info(
                "%s rate limit for %s saved at %d updates per second",
                provider,
                device_key,
                limits[device_key],
            )
        else:
            logger.info(
                "%s rate limit override for %s cleared",
                provider,
                device_key,
            )
        return True

    def _poll(self) -> None:
        self._update_logs()
        while True:
            try:
                callback = self._ui_callbacks.get_nowait()
            except queue.Empty:
                break
            try:
                callback()
            except Exception:
                logger.exception("Could not apply background GUI result")
        if self._closing:
            if (
                (self.bridge is None or not self.bridge.running)
                and not self._busy
                and not self._outputs_starting
                and not self._scan_in_progress
            ):
                if self._outputs_ready and not self._outputs_closed:
                    self.outputs.close()
                    self._outputs_closed = True
                logging.getLogger().removeHandler(self.log_handler)
                self.root.destroy()
                return
        if self.bridge is None or not self.bridge.running:
            if not self._outputs_starting:
                self.status.set("Stopped" if self._outputs_ready else "Scanning lights...")
            self.start_button.configure(
                state=tk.NORMAL if self._outputs_ready else tk.DISABLED
            )
            self.stop_button.configure(state=tk.DISABLED)
            self.test_button.configure(
                state=tk.NORMAL if self._outputs_ready and not self._busy else tk.DISABLED
            )
            for widget in self._config_widgets:
                widget.configure(state=tk.NORMAL)
        elif self.bridge.ready.is_set():
            self.status.set(f"Listening on UDP {config.YARG_UDP_PORT}")
        else:
            self.status.set("Starting — discovering lights")
        if self._stop_in_progress and (self.bridge is None or not self.bridge.running):
            self._stop_in_progress = False
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
            if record == "__RATE_TEST_DONE__":
                self._busy = False
                continue
            self.log.configure(state=tk.NORMAL)
            self.log.insert(tk.END, record + "\n")
            self.log.see(tk.END)
            self.log.configure(state=tk.DISABLED)

    def close(self) -> None:
        self._closing = True
        self._start_after_scan = False
        self._rate_test_stop.set()
        if self._tuya_login_cancel is not None:
            self._tuya_login_cancel.set()
        if self.bridge is not None and self.bridge.running:
            self.bridge.stop(timeout=0.1)


def run_gui() -> None:
    logging.basicConfig(
        level=logging.DEBUG if config.DEBUG_LOGGING else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    _load_settings()
    root = tk.Tk(className="yalcs")
    YargLifxWindow(root)
    root.mainloop()
