"""Exercise an AppImage/folder archive/AppDir with local UDP and no bulbs.

Usage: python3 packaging/linux/check_package.py dist/YALCS-0.2.2-x86_64.AppImage
Add --headless-only when the test machine has no X11/WSLg display.
"""

import argparse
import json
import os
from pathlib import Path
import signal
import socket
import struct
import subprocess
import tempfile
import time


def packet(cue):
    data = bytearray(47)
    struct.pack_into("<IB", data, 0, 0x59415247, 3)
    data[13] = 5
    data[34] = cue
    data[37] = 24
    return bytes(data)


def check_package(path, headless_only=False, mounted=False):
    command = [str(Path(path).resolve())]
    is_appimage = command[0].endswith(".AppImage")
    if is_appimage and not mounted:
        command.append("--appimage-extract-and-run")
    with tempfile.TemporaryDirectory(prefix="yalcs package check ") as directory:
        env = {**os.environ, "XDG_CONFIG_HOME": directory}
        if command[0].endswith(".tar.gz"):
            subprocess.run(["tar", "-xzf", command[0], "-C", directory], check=True, timeout=30)
            command = [str(Path(directory) / "YALCS.AppDir" / "AppRun")]
        subprocess.run(command + ["--self-check", "--headless"], env=env, check=True, timeout=30)
        if not headless_only:
            subprocess.run(command + ["--self-check"], env=env, check=True, timeout=30)
        if is_appimage and not mounted:
            # The extraction runtime forks a supervisor that does not forward
            # SIGTERM. Check the installed payload directly for service shutdown.
            subprocess.run([command[0], "--appimage-extract"], cwd=directory,
                           stdout=subprocess.DEVNULL, check=True, timeout=30)
            command = [str(Path(directory) / "squashfs-root" / "AppRun")]
        with (
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as light,
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sender,
        ):
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
            probe.close()
            light.bind(("127.0.0.1", 4003))
            light.settimeout(0.1)
            state = Path(directory) / "YARG-LIFX"
            state.mkdir()
            (state / "settings.json").write_text(json.dumps({
                "yarg_udp_port": port,
                "lifx_enabled": False, "tuya_enabled": False, "govee_enabled": True,
                "govee_discovery_timeout": 0.1,
                "govee_include_devices": ["127.0.0.1"],
                "manual_lights": [{"provider": "govee", "name": "Loopback emulator", "ip": "127.0.0.1"}],
            }))
            process = subprocess.Popen(command + ["--headless"], env=env,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                def expect(cue, cmd, data):
                    deadline = time.monotonic() + 15
                    while time.monotonic() < deadline:
                        if process.poll() is not None:
                            raise AssertionError("Package exited before sending a light command")
                        sender.sendto(packet(cue), ("127.0.0.1", port))
                        try:
                            message = json.loads(light.recvfrom(4096)[0])["msg"]
                        except socket.timeout:
                            continue
                        if message["cmd"] == cmd and message["data"] == data:
                            return
                    raise AssertionError(f"Missing {cmd} command for cue {cue}: {data}")

                expect(5, "colorwc", {"color": {"r": 0, "g": 0, "b": 255}, "colorTemInKelvin": 0})
                expect(2, "colorwc", {"color": {"r": 255, "g": 255, "b": 0}, "colorTemInKelvin": 0})
                sender.sendto(b"malformed", ("127.0.0.1", port))
                expect(8, "turn", {"value": 0})
                process.send_signal(signal.SIGTERM)
                _, errors = process.communicate(timeout=15)
                if process.returncode != 0:
                    raise AssertionError(errors)
                if "Ignoring malformed UDP packet" not in errors or "bridge stopped" not in errors:
                    raise AssertionError(errors)
                print("Packaged YARG -> Govee UDP, saved settings and SIGTERM: OK")
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("package")
    parser.add_argument("--headless-only", action="store_true")
    parser.add_argument("--mounted", action="store_true", help="also exercise normal FUSE launch and SIGTERM")
    args = parser.parse_args()
    check_package(args.package, args.headless_only, args.mounted)
