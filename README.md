# YARG-LIFX

A small Windows desktop app that listens for YARG's local UDP lighting
datastream and controls LIFX lights using the LAN protocol. The app is
entirely made with AI but has been tested on all my LIFX lights to
work pretty well. This app is meant to replace YALCY since its LIFX
support is seriously lacking, specifically with zones.

## Run from source

Install Python 3.10 or newer. In PowerShell, from this folder:

```powershell
py -m venv .venv-yarg-lifx
.\.venv-yarg-lifx\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python main.py
```

The window lets you configure the YARG port, LIFX discovery/rediscovery
intervals, brightness, transition time, optional included bulbs, and event
logging. Settings are saved to `%APPDATA%\YARG-LIFX\settings.json`. Click
**Start listening** after enabling YARG's **UDP data stream** option. Use
**Test lights** to set all included lights to a chosen color. Settings are
locked while the listener is running; stop it before editing, then restart to
apply changes.

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
uses timed on/off pulses for strobe. The transition setting controls LIFX color
fades. Commands are sent on worker threads so the UDP listener can keep up with
gameplay.

## Protocol references

- [YARG DataStreamController](https://github.com/YARC-Official/YARG/blob/master/Assets/Script/Integration/Data%20Stream/DataStreamController.cs):
  UDP destination, header, version, and field serialization.
- [YARG.Core LightingEvent.cs](https://github.com/YARC-Official/YARG.Core/blob/master/YARG.Core/Chart/Venue/LightingEvent.cs):
  authoritative cue enum values.
- [YALCY UDP intake](https://github.com/YARC-Official/YALCY/blob/master/YALCY/Udp/UdpIntake.cs)
  and [byte descriptions](https://github.com/YARC-Official/YALCY/blob/master/YALCY/Udp/UdpIntake.Enums.cs):
  extended datagram layouts and enum descriptions.
- [LIFX LAN protocol](https://lan.developer.lifx.com/) is used by
  `lifxlan`; no cloud API or token is required.

