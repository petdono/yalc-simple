"""Small set of runtime settings for YARG-LIFX."""

YARG_UDP_PORT = 36107

LIFX_DISCOVERY_TIMEOUT = 2.0
BRIGHTNESS_MULTIPLIER = 0.8
LIFX_ENABLED = True

# Empty means every discovered light; entries may be a label or IP address.
INCLUDE_LIGHTS: tuple[str, ...] = ()
EXCLUDE_LIGHTS: tuple[str, ...] = ()

GOVEE_ENABLED = True
GOVEE_DISCOVERY_TIMEOUT = 2.0
# Entries may be a device IP, device ID, or SKU; empty means all.
GOVEE_INCLUDE_DEVICES: tuple[str, ...] = ()

TUYA_ENABLED = True
TUYA_MAX_UPDATES_PER_SECOND = 10
LIGHT_RATE_LIMITS: dict[str, int] = {}

# Entries are (provider, label, IP address, MAC address); Govee MACs are blank.
MANUAL_LIGHTS: tuple[tuple[str, str, str, str], ...] = ()

DEBUG_LOGGING = True
COLOR_TRANSITION_MS = 120
