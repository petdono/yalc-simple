# Yet Another Lighting Controller, Simplified
![Banner](resource-images/image.png)
## What is it?
controls smart lights based on what's happening in YARG. The app is
entirely made with AI but has been tested on all my LIFX & SmartLife lights to
work pretty well, as well as being tested with a Govee UDP emulator script. 
This app is meant to replace YALCY since its LIFX support is seriously lacking,
specifically the requirement for each zone to either be red, green, or blue.

## Supported Devices
- Any LIFX light ([Models](https://www.lifx.com/collections/all))
  - Must be connected to the same network and have full RGB support. Tested and functional with LIFX 15" Ceiling.
  - Polychrome zones are not supported, whole light only.
- A Govee light that has LAN support ([Models](https://app-h5.govee.com/user-manual/wlan-guide))
  - If the device isn't listed there, your model is either cloud-only or Bluetooth only. Neither are supported.
- Compatible Smart Life / Tuya Wi-Fi lighting products
  - Only devices advertised as lights with locally supported controls and a TinyTuya-recognized datapoint layout are enabled.
  - Your device must be connected through SmartLife, not Tuya.
  - Some devices may need their rate limits adjusted.
  - Only devices with protocol versions 3.1-3.5 will work. Tested with OhLux bulbs.

## Run from source

Install Python 3.10 or newer. On Debian/Ubuntu, install Tkinter if your Python
installation does not include it (`sudo apt install python3-tk`). From this
folder, create and activate a virtual environment:

```bash
python3 -m venv .venv-linux
source .venv-linux/bin/activate
python -m pip install -r requirements.txt
python main.py
```

On Windows, use PowerShell:

```powershell
py -m venv .venv-yarg-lifx
.\.venv-yarg-lifx\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The main window provides **Settings**, **Manage Lights**, and **Devices**
pop-outs. Settings contains the YARG port, discovery timeouts, brightness,
transition time, optional included bulbs, Govee filtering, and event logging.
The app scans once on launch; use **Scan lights** in Manage Lights to manually
refresh discovered devices. Manage Lights also lets you exclude lights or
identify one with a brief flash. Devices enables/disables LIFX, Govee, and
Smart Life / Tuya, links a Smart Life account, and lets you add LIFX/Govee
devices manually (both require a name and IP; LIFX also requires its MAC
address). Settings are saved to
`%APPDATA%\YARG-LIFX\settings.json` on Windows, or
`${XDG_CONFIG_HOME:-~/.config}/YARG-LIFX/settings.json` on Linux. Click **Start listening** after enabling
YARG's **UDP data stream** option. Use **Test lights** to set included lights
to a chosen color. **Rate limit tester** opens a separate window to select a
discovered light, preview a changing color sequence as commands are sent, and
set the selected light's maximum updates per second. Manage Lights also has a
per-light rate field. A blank Tuya field uses the default Tuya limit from
Settings; blank LIFX/Govee fields mean no rate limit. Explicit per-light limits
are supported for all providers. Stop YARG listening before running the rate
test. Activity logs confirm when the first valid YARG datagram arrives and
report malformed packets. Settings and provider
controls are locked while the listener is running; stop it before changing
them, then restart to apply the changes.

## Govee LAN Control setup

Enable **LAN Control** for each compatible Wi-Fi device in the Govee Home app
under **Devices → (device) → Settings/More Settings → LAN Control**. Govee
devices without LAN API support or with that setting disabled will not respond
to discovery. Allow YARG-LIFX through your firewall for local network traffic
if discovery or UDP reception is blocked.

The app sends the documented discovery JSON to multicast
`239.255.255.250:4001`, listens for responses on UDP `4002`, and sends commands
to each discovered device at UDP `4003`. It controls every discovered Govee
device by default. The **Devices** pop-out enables or disables Govee; the
**Settings** pop-out contains the discovery timeout and optional
comma-separated IP/device-ID/SKU filter. No API key, cloud connection, MQTT,
or additional Govee Python package is used.

## Smart Life / Tuya setup

1. Start YARG-LIFX and open **Devices** → **Connect / refresh**, or run
   `python main.py --tuya-login` from a terminal.
2. Enter the Smart Life **User Code** from **Me → Settings → Account and
   Security → User Code**.
3. Scan the displayed QR code in Smart Life (**+ → Scan**) and approve the
   authorization.
4. The app retrieves device metadata and local keys for devices the account
   advertises as locally controllable lights. Non-light products and lights
   without a verified TinyTuya datapoint mapping are skipped.
5. The app stores the supported device credentials in
`%APPDATA%\YARG-LIFX\tuya_devices.json` on Windows, or
`${XDG_CONFIG_HOME:-~/.config}/YARG-LIFX/tuya_devices.json` on Linux. This file contains
   sensitive local keys; it is kept outside the repository and should not be
   shared. Tuya credentials are separate from the normal settings file.

The QR authorization and device-credential retrieval use the Tuya
[Device Sharing SDK](https://github.com/tuya/tuya-device-sharing-sdk); the
Smart Life user-code/QR flow is also documented by
[tuya-local-key](https://github.com/vineetchoudhary/tuya-local-key). No Tuya
developer project or manually extracted local key is required.

**Internet/Tuya cloud is used during QR onboarding or an explicit credential
refresh only. Normal startup discovers cached devices on the LAN, and YARG
lighting commands are sent locally with TinyTuya; the gameplay path does not
use the Tuya cloud.** TinyTuya detects and validates the local bulb datapoint
layout before the app enables control. Unsupported layouts are skipped rather
than receiving guessed datapoints. Tuya commands run on a worker, duplicate
states are cached, and **Settings** lets you cap updates per second (default
10).

The Tuya scan combines UDP discovery with a credential-assisted IP scan of
the active IPv4 subnet. The log reports which subnet was scanned and how many
responses it received. If saved lights are reported as not reachable, confirm
the computer and lights are on the same non-guest LAN, client/AP isolation is
off, and the firewall allows local Tuya UDP discovery (ports 6666, 6667, and
7000) and connections to devices on TCP port 6668. WSL2 NAT commonly does not
forward LAN broadcasts; use the Windows app or run the Linux app on a native
Linux host when testing LAN discovery.

Changing, resetting, or re-pairing a Tuya device may invalidate its local key.
Use **Devices → Connect / refresh** or `python main.py --tuya-refresh` to
reauthorize and replace the cached device data. To remove only the Tuya data,
run `python main.py --tuya-logout`.

Allow the app through your operating system's firewall on your private LAN if
UDP or LIFX discovery is blocked.

## Build a standalone executable

From PowerShell, create the build environment and run the build script:

```powershell
py -m venv .venv-yarg-lifx
.\.venv-yarg-lifx\Scripts\Activate.ps1
python -m pip install -r requirements-build.txt
.\build.ps1
```

The one-file, windowed executable is `dist\YALCS-0.2.1.exe`. Copy it anywhere and
run it; per-user settings remain in AppData. The executable has no console
window. Windows may ask you to allow local network access the first time it
runs.

### Linux AppImage

Linux releases now use an AppImage with a normal directory bundle inside it.
Python, Tcl/Tk and Python dependencies are included; users do not need to
install Python or Tkinter. Keep the AppImage in a permanent folder such as
`~/Applications`, make it executable, and run it:

```bash
chmod +x YALCS-0.2.2-x86_64.AppImage
./YALCS-0.2.2-x86_64.AppImage
```

Use the package matching your CPU (`x86_64` or `aarch64`). Run as your normal
desktop user. Replacing the AppImage upgrades the application while preserving
settings and Smart Life credentials in `~/.config/YARG-LIFX` (or the absolute
`$XDG_CONFIG_HOME/YARG-LIFX` path). Existing settings from 0.2.1 are reused.
Headless and test modes now load those same saved settings.

If FUSE is unavailable, run without mounting:

```bash
./YALCS-0.2.2-x86_64.AppImage --appimage-extract-and-run
```

A folder package is also produced for systems where AppImages are inconvenient:

```bash
tar -xzf YALCS-0.2.2-linux-x86_64.tar.gz
./YALCS.AppDir/AppRun
```

Keep the entire `YALCS.AppDir` together; its launcher works from any directory,
including paths with spaces. Both formats include a desktop entry and icon for
desktop integration. If the download is on a `noexec` filesystem, move it to
an executable filesystem before launching it.

To diagnose a package without contacting or changing lights:

```bash
./YALCS-0.2.2-x86_64.AppImage --self-check
./YALCS-0.2.2-x86_64.AppImage --self-check --headless
```

The first command also creates and closes a Tk window to check the GUI bundle.
The second checks dependencies and Tcl without a display. On Wayland desktops,
Tk uses XWayland; a working `DISPLAY` is required for the GUI. `--headless`
works without a graphical session and handles SIGTERM for session/service
shutdown. For services, use the folder package's AppRun: the AppImage
extract-and-run supervisor does not forward SIGTERM to its child. All existing
CLI options also work with AppRun or the AppImage.

### Build Linux packages

Build on Linux or WSL, using the architecture you intend to distribute.
Ubuntu 22.04 is the current CI baseline (glibc 2.35); building on a newer
distribution can require newer system libraries on users' computers.

```bash
sudo apt install python3 python3-venv python3-tk curl desktop-file-utils
bash build_linux.sh
```

The script runs the unit tests, builds an `onedir` payload with PyInstaller,
assembles an AppDir with its launcher/desktop entry/icon, and packages:

- `dist/YALCS-0.2.2-x86_64.AppImage`
- `dist/YALCS-0.2.2-linux-x86_64.tar.gz`
- SHA-256 checksum files for both

The build environment and temporary build tree live under
`${XDG_CACHE_HOME:-~/.cache}/yalcs`, avoiding Windows/OneDrive filesystem
issues in WSL. appimagetool 1.9.1 is downloaded from its official release and
verified against a pinned SHA-256 checksum. Its runtime may also be downloaded
during packaging. Building does not require FUSE.

Overrides: `PYTHON`, `VENV_DIR`, `DIST_DIR`, `VERSION`, `APPIMAGETOOL`
(path to a custom appimagetool AppImage), and `APPIMAGE_RUNTIME` (path to a
custom runtime). Set `APPDIR_ONLY=1` to build only the folder archive.

The build checks imports and bundled Tcl before producing release artifacts.
The GitHub Actions Linux workflow additionally tests Tk under Xvfb and runs the
packaged UDP bridge against a loopback Govee emulator:

```bash
python3 packaging/linux/check_package.py dist/YALCS-0.2.2-x86_64.AppImage
```

Add `--headless-only` to that check on a machine without a display, or `--mounted`
to additionally test normal FUSE launch. Tests use
temporary settings and never control physical bulbs. WSL is suitable for
packaging and these tests, but WSL2 NAT does not reliably forward discovery
broadcasts from physical lights. Test real devices on a native Linux LAN host
or use the Windows application.

For command-line use from source:

```powershell
python main.py --test       # discover lights and set them blue once
python main.py --headless   # listen without opening the window; Ctrl+C stops
python main.py --tuya-login # link or refresh Smart Life credentials
python main.py --tuya-logout # delete only cached Smart Life credentials
```

## Lighting

YARG sends enum values for the current lighting cue, section, and strobe state;
it does not send RGB values. The app maps Verse to blue, Chorus to yellow, and
other cue families to practical colors, turns lights off for blackout cues, and
uses timed on/off pulses for strobe. Each enabled provider receives the same
internal lighting intent. LIFX uses its color transition command; Govee uses
its local `colorwc`, `brightness`, and `turn` messages; Tuya uses TinyTuya's
local bulb interface. Commands run outside the UDP receiver, with cached
per-device state to avoid identical commands.

## Protocol references

- [YARG DataStreamController](https://github.com/YARC-Official/YARG/blob/master/Assets/Script/Integration/Data%20Stream/DataStreamController.cs):
  UDP destination, header, version, and field serialization.
- [YARG.Core LightingEvent.cs](https://github.com/YARC-Official/YARG.Core/blob/master/YARG.Core/Chart/Venue/LightingEvent.cs):
  authoritative cue enum values.
- [YALCY UDP intake](https://github.com/YARC-Official/YALCY/blob/master/YALCY/Udp/UdpIntake.cs)
  and [byte descriptions](https://github.com/YARC-Official/YALCY/blob/master/YALCY/Udp/UdpIntake.Enums.cs):
  extended datagram layouts and enum descriptions.
- [Govee LAN Control user guide](https://app-h5.govee.com/user-manual/wlan-guide):
  vendor instructions for enabling LAN Control.
- [Govee LAN protocol documentation](https://github.com/egold555/Govee-Reverse-Engineering/blob/master/Products/H619D.md):
  discovery ports, response fields, and `turn`, `brightness`, `colorwc`, and
  color-temperature command payloads. Device support varies by model.
- [govee-lan Python library](https://github.com/blakete/govee-lan):
  maintained implementation cross-checking the multicast scan and command
  formats. YARG-LIFX uses the small JSON LAN protocol directly to avoid
  additional dependencies and subnet sweeps.
- [LIFX LAN protocol](https://lan.developer.lifx.com/) is used by
  `lifxlan`; no cloud API or token is required.
