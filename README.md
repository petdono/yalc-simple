# YARG-LIFX

A small Windows desktop app that listens for YARG's local UDP lighting
datastream and controls LIFX and Govee lights using their LAN protocols. No
cloud account, token, web server, or database is involved.

## Run from source

Install Python 3.10 or newer. In PowerShell, from this folder:

```powershell
py -m venv .venv-yarg-lifx
.\.venv-yarg-lifx\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The window lets you configure the YARG port, discovery/rediscovery intervals,
brightness, transition time, optional included bulbs, Govee enablement and
filtering, and event logging. Settings are saved to
`%APPDATA%\YARG-LIFX\settings.json`. Click **Start listening** after enabling
YARG's **UDP data stream** option. Use **Test lights** to set all included
lights to a chosen color. Settings are locked while the listener is running;
stop it before editing, then restart to apply changes.

## Govee LAN Control setup

Enable **LAN Control** for each compatible Wi-Fi device in the Govee Home app
under **Devices → (device) → Settings/More Settings → LAN Control**. Govee
devices without LAN API support or with that setting disabled will not respond
to discovery. Make sure Windows Firewall allows YARG-LIFX on your private LAN.

The app sends the documented discovery JSON to multicast
`239.255.255.250:4001`, listens for responses on UDP `4002`, and sends commands
to each discovered device at UDP `4003`. It controls every discovered Govee
device by default. The Govee checkbox, discovery timeout, and optional
comma-separated IP/device-ID/SKU filter are available in the configuration
window. No API key, cloud connection, MQTT, or additional Govee Python package
is used.

Allow the app through Windows Firewall on your private LAN if UDP or LIFX
discovery is blocked.

## Build a standalone executable

From PowerShell, create the build environment and run the build script:

```powershell
py -m venv .venv-yarg-lifx
.\.venv-yarg-lifx\Scripts\Activate.ps1
python -m pip install -r requirements-build.txt
.\build.ps1
```

The one-file, windowed executable is `dist\YARG-LIFX.exe`. Copy it anywhere and
run it; per-user settings remain in AppData. The executable has no console
window. Windows may ask you to allow local network access the first time it
runs.

For command-line use from source:

```powershell
python main.py --test       # discover lights and set them blue once
python main.py --headless   # listen without opening the window; Ctrl+C stops
```

## Lighting

YARG sends enum values for the current lighting cue, section, and strobe state;
it does not send RGB values. The app maps Verse to blue, Chorus to yellow, and
other cue families to practical colors, turns lights off for blackout cues, and
uses timed on/off pulses for strobe. Both brands receive the same internal
lighting intent. LIFX uses its color transition command; Govee uses its local
`colorwc`, `brightness`, and `turn` messages (the YARG events provide colors,
not a color temperature). Commands run outside the UDP receiver, with cached
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
