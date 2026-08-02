"""Hardware actions and device recovery integration."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, cast

from devolo_plc_api import Device

from devolo_watchdog.config import Settings

LOG = logging.getLogger("devolo-throughput-watchdog")
DeviceFactory = Callable[..., Any]


def read_password(path: str | None) -> str | None:
    """Read password from file if path is specified."""
    if not path:
        return None
    try:
        password = Path(path).read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"DW_PASSWORD_FILE unreadable ({path}): {exc}") from None
    if not password:
        raise ValueError(f"DW_PASSWORD_FILE is empty ({path})")
    return password


async def async_restart_devolo(
    settings: Settings,
    *,
    device_class: DeviceFactory | None = None,
) -> bool:
    """Reboot the specified devolo device asynchronously via its management API."""
    if device_class is None:
        device_factory: DeviceFactory = Device

        from devolo_watchdog.probes import patch_devolo_device_interfaces

        patch_devolo_device_interfaces(device_factory)
    else:
        device_factory = device_class

    device = device_factory(ip=settings.devolo_ip)
    if password := read_password(settings.password_file):
        device.password = password
    async with device:
        if device.device is None:
            err = f"Devolo device at {settings.devolo_ip} does not support Device API"
            raise RuntimeError(err)
        return cast(bool, await device.device.async_restart())
    raise RuntimeError("Devolo device context suppressed the restart failure")


def restart_devolo(settings: Settings, *, device_class: DeviceFactory | None = None) -> bool:
    """Reboot the specified devolo device via its management API."""
    return asyncio.run(async_restart_devolo(settings, device_class=device_class))
