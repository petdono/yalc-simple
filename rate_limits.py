"""Optional per-device lighting rate limits."""

from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from typing import Generator

import config

_registry_lock = threading.Lock()
_device_locks: dict[str, threading.Lock] = {}
_last_updates: dict[str, float] = {}


def rate_limit_for(provider: str, identity: str) -> int | None:
    key = f"{provider.casefold()}:{identity.casefold()}"
    override = config.LIGHT_RATE_LIMITS.get(key)
    if override is not None:
        return max(1, min(50, override))
    if provider.casefold() == "tuya":
        return max(1, min(50, config.TUYA_MAX_UPDATES_PER_SECOND))
    return None


@contextmanager
def rate_limited(provider: str, identity: str) -> Generator[None, None, None]:
    rate = rate_limit_for(provider, identity)
    if rate is None:
        yield
        return

    key = f"{provider.casefold()}:{identity.casefold()}"
    with _registry_lock:
        device_lock = _device_locks.setdefault(key, threading.Lock())
    with device_lock:
        interval = 1.0 / rate
        wait = _last_updates.get(key, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_updates[key] = time.monotonic()
        yield
