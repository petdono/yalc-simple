"""Small set of runtime settings for YARG-LIFX."""

YARG_UDP_PORT = 36107

LIFX_DISCOVERY_TIMEOUT = 2.0
LIFX_REDISCOVERY_INTERVAL = 30.0
BRIGHTNESS_MULTIPLIER = 0.8

# Empty means every discovered light; entries may be a label or IP address.
INCLUDE_LIGHTS: tuple[str, ...] = ()

DEBUG_LOGGING = True
COLOR_TRANSITION_MS = 120

