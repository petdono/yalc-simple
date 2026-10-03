"""Start the YARG-LIFX desktop application or command-line bridge."""

from __future__ import annotations

import argparse
import logging
import time

import config
from runtime import YargBridge, run_test


def main() -> int:
    parser = argparse.ArgumentParser(description="Control LIFX lights from YARG.")
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
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if config.DEBUG_LOGGING else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    if args.test:
        return run_test()
    if args.headless:
        bridge = YargBridge()
        bridge.start()
        try:
            while bridge.running:
                time.sleep(0.5)
        except KeyboardInterrupt:
            logging.info("Shutting down")
        finally:
            bridge.stop()
        return 0

    from gui import run_gui

    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
