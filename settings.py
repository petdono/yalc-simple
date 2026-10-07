"""Shared settings validation and persistence loading for GUI and CLI."""

from __future__ import annotations

import ipaddress
import json
import logging
import re
from typing import Any

import config
from tuya import STATE_FILE

logger = logging.getLogger(__name__)
SETTINGS_DIRECTORY = STATE_FILE.parent
SETTINGS_FILE = SETTINGS_DIRECTORY / "settings.json"


def _current_settings() -> dict[str, Any]:
    return {
        "yarg_udp_port": config.YARG_UDP_PORT,
        "lifx_discovery_timeout": config.LIFX_DISCOVERY_TIMEOUT,
        "brightness_multiplier": config.BRIGHTNESS_MULTIPLIER,
        "lifx_enabled": config.LIFX_ENABLED,
        "govee_enabled": config.GOVEE_ENABLED,
        "tuya_enabled": config.TUYA_ENABLED,
        "tuya_max_updates_per_second": config.TUYA_MAX_UPDATES_PER_SECOND,
        "light_rate_limits": dict(config.LIGHT_RATE_LIMITS),
        "govee_discovery_timeout": config.GOVEE_DISCOVERY_TIMEOUT,
        "govee_include_devices": list(config.GOVEE_INCLUDE_DEVICES),
        "color_transition_ms": config.COLOR_TRANSITION_MS,
        "include_lights": list(config.INCLUDE_LIGHTS),
        "exclude_lights": list(config.EXCLUDE_LIGHTS),
        "manual_lights": [
            {"provider": provider, "name": name, "ip": ip, "mac": mac}
            for provider, name, ip, mac in config.MANUAL_LIGHTS
        ],
        "debug_logging": config.DEBUG_LOGGING,
    }


def _normalize_manual_lights(
    manual_lights: Any,
) -> tuple[tuple[str, str, str, str], ...]:
    if not isinstance(manual_lights, list):
        raise ValueError("Manual lights must be a list of device objects.")
    normalized: list[tuple[str, str, str, str]] = []
    for item in manual_lights:
        if not isinstance(item, dict):
            raise ValueError("Each manual light must be a device object.")
        provider, name, ip, mac = (
            item.get("provider"),
            item.get("name"),
            item.get("ip"),
            item.get("mac", ""),
        )
        if (
            not isinstance(provider, str)
            or provider.lower() not in {"lifx", "govee"}
            or not isinstance(name, str)
            or not name.strip()
            or not isinstance(ip, str)
            or not isinstance(mac, str)
        ):
            raise ValueError("Manual lights need a provider, name, and IP address.")
        try:
            address = ipaddress.ip_address(ip.strip())
        except ValueError as exc:
            raise ValueError(f"Invalid manual light IP address: {ip}") from exc
        if address.version != 4:
            raise ValueError("Manual light IP addresses must be IPv4.")
        normalized_mac = mac.strip().lower()
        if provider.lower() == "lifx" and not re.fullmatch(
            r"(?:[0-9a-f]{2}:){5}[0-9a-f]{2}", normalized_mac
        ):
            raise ValueError("Manual LIFX lights need a MAC address.")
        if provider.lower() == "govee" and normalized_mac:
            raise ValueError("Govee manual lights do not use a MAC address.")
        normalized.append(
            (provider.lower(), name.strip(), str(address), normalized_mac)
        )
    return tuple(normalized)


def _light_exclusions(
    current: set[str], ip_address: str, enabled: bool
) -> set[str]:
    exclusions = {
        value.casefold(): value
        for value in current
        if value.casefold() != ip_address.casefold()
    }
    if not enabled:
        exclusions[ip_address.casefold()] = ip_address
    return set(exclusions.values())


def _apply_settings(settings: dict[str, Any]) -> None:
    port = int(settings.get("yarg_udp_port", config.YARG_UDP_PORT))
    discovery_timeout = float(
        settings.get("lifx_discovery_timeout", config.LIFX_DISCOVERY_TIMEOUT)
    )
    brightness = float(
        settings.get("brightness_multiplier", config.BRIGHTNESS_MULTIPLIER)
    )
    lifx_enabled = settings.get("lifx_enabled", config.LIFX_ENABLED)
    govee_enabled = settings.get("govee_enabled", config.GOVEE_ENABLED)
    tuya_enabled = settings.get("tuya_enabled", config.TUYA_ENABLED)
    tuya_rate = int(
        settings.get(
            "tuya_max_updates_per_second", config.TUYA_MAX_UPDATES_PER_SECOND
        )
    )
    light_rates = settings.get("light_rate_limits", dict(config.LIGHT_RATE_LIMITS))
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
    exclude_lights = settings.get("exclude_lights", list(config.EXCLUDE_LIGHTS))
    manual_lights = settings.get(
        "manual_lights",
        [
            {"provider": provider, "name": name, "ip": ip, "mac": mac}
            for provider, name, ip, mac in config.MANUAL_LIGHTS
        ],
    )
    debug_logging = settings.get("debug_logging", config.DEBUG_LOGGING)

    if not 1 <= port <= 65535:
        raise ValueError("YARG UDP port must be between 1 and 65535.")
    if not 0.1 <= discovery_timeout <= 30:
        raise ValueError("LIFX discovery timeout must be between 0.1 and 30 seconds.")
    if not 0 <= brightness <= 1:
        raise ValueError("Brightness multiplier must be between 0 and 1.")
    if (
        not isinstance(lifx_enabled, bool)
        or not isinstance(govee_enabled, bool)
        or not isinstance(tuya_enabled, bool)
    ):
        raise ValueError("Provider enabled settings must be true or false.")
    if not 1 <= tuya_rate <= 50:
        raise ValueError("Tuya updates per second must be between 1 and 50.")
    if not isinstance(light_rates, dict):
        raise ValueError("Per-light rate limits must be an object.")
    normalized_light_rates: dict[str, int] = {}
    for device_key, value in light_rates.items():
        if (
            not isinstance(device_key, str)
            or not re.fullmatch(r"(?:lifx|govee|tuya):.+", device_key.casefold())
            or isinstance(value, bool)
        ):
            raise ValueError("Per-light rate limits need a provider-qualified device ID.")
        try:
            rate = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("Per-light rates must be whole numbers from 1 to 50.") from exc
        if not 1 <= rate <= 50:
            raise ValueError("Per-light rates must be between 1 and 50.")
        normalized_light_rates[device_key.casefold()] = rate
    legacy_tuya_rates = settings.get("tuya_light_rate_limits", {})
    if isinstance(legacy_tuya_rates, dict):
        for device_id, value in legacy_tuya_rates.items():
            key = f"tuya:{str(device_id).casefold()}"
            if key not in normalized_light_rates:
                try:
                    rate = int(value)
                except (TypeError, ValueError):
                    continue
                if 1 <= rate <= 50:
                    normalized_light_rates[key] = rate
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
    if not isinstance(exclude_lights, list) or any(
        not isinstance(item, str) for item in exclude_lights
    ):
        raise ValueError("Excluded lights must be a list of labels or IP addresses.")
    normalized_manual_lights = _normalize_manual_lights(manual_lights)
    if not isinstance(debug_logging, bool):
        raise ValueError("Debug logging setting must be true or false.")

    config.YARG_UDP_PORT = port
    config.LIFX_DISCOVERY_TIMEOUT = discovery_timeout
    config.BRIGHTNESS_MULTIPLIER = brightness
    config.LIFX_ENABLED = lifx_enabled
    config.GOVEE_ENABLED = govee_enabled
    config.TUYA_ENABLED = tuya_enabled
    config.TUYA_MAX_UPDATES_PER_SECOND = tuya_rate
    config.LIGHT_RATE_LIMITS = normalized_light_rates
    config.GOVEE_DISCOVERY_TIMEOUT = govee_discovery_timeout
    config.GOVEE_INCLUDE_DEVICES = tuple(
        item.strip() for item in govee_include_devices if item.strip()
    )
    config.COLOR_TRANSITION_MS = transition
    config.INCLUDE_LIGHTS = tuple(item.strip() for item in include_lights if item.strip())
    config.EXCLUDE_LIGHTS = tuple(
        item.strip() for item in exclude_lights if item.strip()
    )
    config.MANUAL_LIGHTS = normalized_manual_lights
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
