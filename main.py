"""Start the YARG-LIFX desktop application or command-line bridge."""

from __future__ import annotations

import argparse
import logging
import signal
import time

import config
from runtime import YargBridge, run_test
from settings import _load_settings
from tuya import TuyaSetupError, connect_account, logout, print_qr


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Control LIFX, Govee, and Smart Life lights from YARG."
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="discover lights, set them blue once, then exit",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="run without opening the configuration window",
    )
    parser.add_argument(
        "--self-check", action="store_true",
        help="check bundled dependencies and Tcl/Tk, then exit (add --headless for no display)",
    )
    tuya_actions = parser.add_mutually_exclusive_group()
    tuya_actions.add_argument(
        "--tuya-login",
        "--tuya-refresh",
        dest="tuya_action",
        action="store_const",
        const="login",
        help="link or refresh a Smart Life account using QR authorization",
    )
    tuya_actions.add_argument(
        "--tuya-logout",
        dest="tuya_action",
        action="store_const",
        const="logout",
        help="delete only the cached Smart Life device credentials",
    )
    parser.add_argument(
        "--tuya-user-code",
        help="Smart Life user code (otherwise prompted during QR login)",
    )
    args = parser.parse_args()
    if args.tuya_user_code and args.tuya_action != "login":
        parser.error("--tuya-user-code requires --tuya-login or --tuya-refresh")

    if args.self_check:
        return self_check(headless=args.headless)
    _load_settings()
    logging.basicConfig(
        level=logging.DEBUG if config.DEBUG_LOGGING else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.tuya_action == "logout":
        if logout():
            print("Cached Smart Life device credentials deleted.")
        else:
            print("No cached Smart Life device credentials were found.")
        return 0
    if args.tuya_action == "login":
        user_code = args.tuya_user_code or input(
            "Enter the Smart Life user code (Me > Settings > Account and Security > User Code): "
        ).strip()
        if not user_code:
            print("A Smart Life user code is required.")
            return 2

        def show_qr(payload: str) -> None:
            print("\nScan this QR code in Smart Life (+ > Scan), then approve:\n")
            print_qr(payload)

        try:
            names = connect_account(
                user_code,
                on_qr=show_qr,
                on_status=print,
            )
        except TuyaSetupError as exc:
            print(f"Smart Life setup failed: {exc}")
            return 1
        print(f"\nAccount connected. Found {len(names)} locally controllable light(s).")
        for name in names:
            print(f"  {name}")
        print("Local device credentials saved.")
        return 0
    if args.test:
        return run_test()
    if args.headless:
        bridge = YargBridge()
        # Desktop/session managers and systemd stop Linux processes with SIGTERM.
        previous_handler = signal.signal(signal.SIGTERM, lambda *_: bridge.stop())
        bridge.start()
        try:
            while bridge.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            logging.info("Shutting down")
        finally:
            bridge.stop()
            signal.signal(signal.SIGTERM, previous_handler)
        return 0

    from gui import run_gui

    run_gui()
    return 0


def self_check(headless: bool = False) -> int:
    """Validate the actual installed payload without discovering or changing lights."""
    try:
        import importlib
        import tkinter as tk

        for module in ("lifxlan", "tinytuya", "psutil", "tuya_sharing", "qrcode", "certifi", "gui"):
            importlib.import_module(module)
        interpreter = tk.Tcl()
        print(f"Tcl {interpreter.call('info', 'patchlevel')}: OK")
        if not headless:
            root = tk.Tk(className="yalcs")
            try:
                root.withdraw()
                root.update()
                print(f"Tk {root.call('package', 'require', 'Tk')}: OK")
            finally:
                root.destroy()
        print("YALCS dependencies: OK")
        return 0
    except Exception as exc:
        print(f"YALCS self-check failed: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
