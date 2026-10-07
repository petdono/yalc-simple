"""Regression checks for Linux sessions, shared settings and Tk worker lifetime."""

import json
import os
import queue
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import config
import main
import settings
from gui import YargLifxWindow


class SharedSettingsTests(unittest.TestCase):
    def test_headless_loads_saved_settings_before_creating_outputs(self):
        original_port = config.YARG_UDP_PORT
        original_lifx = config.LIFX_ENABLED
        bridge = Mock()
        bridge.running = False
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "settings.json"
            path.write_text(json.dumps({"yarg_udp_port": 45678, "lifx_enabled": False}))
            try:
                def create_bridge():
                    self.assertEqual(config.YARG_UDP_PORT, 45678)
                    self.assertFalse(config.LIFX_ENABLED)
                    return bridge
                with (
                    patch.object(settings, "SETTINGS_FILE", path),
                    patch.object(sys, "argv", ["main.py", "--headless"]),
                    patch.object(main, "YargBridge", side_effect=create_bridge),
                ):
                    self.assertEqual(main.main(), 0)
                bridge.start.assert_called_once()
                bridge.stop.assert_called_once()
            finally:
                config.YARG_UDP_PORT = original_port
                config.LIFX_ENABLED = original_lifx

    def test_worker_dispatch_does_not_call_tk(self):
        window = YargLifxWindow.__new__(YargLifxWindow)
        window._ui_callbacks = queue.Queue()
        window.root = Mock()
        callback = Mock()
        worker = threading.Thread(target=window._schedule_ui, args=(callback,))
        worker.start()
        worker.join(1)
        self.assertFalse(worker.is_alive())
        window.root.assert_not_called()
        self.assertEqual(window.root.method_calls, [])
        callback.assert_not_called()
        window._ui_callbacks.get_nowait()()
        callback.assert_called_once()


@unittest.skipUnless(sys.platform == "linux", "Linux session behavior")
class LinuxSessionTests(unittest.TestCase):
    def test_sigterm_stops_headless_listener_cleanly(self):
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "YARG-LIFX"
            state.mkdir()
            (state / "settings.json").write_text(json.dumps({
                "lifx_enabled": False, "govee_enabled": False, "tuya_enabled": False,
                "yarg_udp_port": port,
            }))
            process = subprocess.Popen(
                [sys.executable, "main.py", "--headless"],
                cwd=Path(__file__).parent,
                env={**os.environ, "XDG_CONFIG_HOME": directory},
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            )
            try:
                deadline = time.monotonic() + 10
                while time.monotonic() < deadline:
                    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
                        try:
                            probe.bind(("127.0.0.1", port))
                        except OSError:
                            break
                    if process.poll() is not None:
                        self.fail("Headless process exited before binding")
                    time.sleep(0.05)
                else:
                    self.fail("Listener never bound its saved UDP port")
                process.send_signal(signal.SIGTERM)
                _, errors = process.communicate(timeout=10)
                self.assertEqual(process.returncode, 0, errors)
                self.assertIn("bridge stopped", errors)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


@unittest.skipUnless(os.environ.get("DISPLAY") or sys.platform == "win32", "Needs an X11/WSLg display")
class GuiLifecycleTests(unittest.TestCase):
    def test_close_during_scan_waits_and_closes_outputs(self):
        import tkinter as tk
        root = tk.Tk()
        root.withdraw()
        errors = []
        root.report_callback_exception = lambda *exc: errors.append(exc)
        outputs = Mock()
        outputs.discovered_devices.return_value = []
        started = threading.Event()
        release = threading.Event()
        outputs.start.side_effect = lambda: (started.set(), release.wait(3))
        with patch("gui.LightingOutputs", return_value=outputs):
            window = YargLifxWindow(root)
        root.after(150, window.close)
        root.after(250, release.set)
        root.after(5000, root.quit)
        try:
            root.mainloop()
            self.assertTrue(started.is_set())
            outputs.close.assert_called_once()
            self.assertEqual(errors, [])
        finally:
            release.set()
            if not window._outputs_closed:
                root.destroy()


if __name__ == "__main__":
    unittest.main()
