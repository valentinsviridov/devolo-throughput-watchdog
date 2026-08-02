"""Hardware actions and device recovery integration."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from devolo_watchdog.config import Settings

LOG = logging.getLogger("devolo-throughput-watchdog")


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


async def async_restart_devolo(settings: Settings) -> bool:
    """Reboot the specified devolo device asynchronously via its management API."""
    try:
        from devolo_plc_api import Device
    except ImportError:
        raise RuntimeError("devolo_plc_api library is not installed") from None

    from devolo_watchdog.probes import patch_devolo_device_interfaces

    patch_devolo_device_interfaces()

    device = Device(ip=settings.devolo_ip)
    if password := read_password(settings.password_file):
        device.password = password
    async with device:
        if device.device is None:
            err = f"Devolo device at {settings.devolo_ip} does not support Device API"
            raise RuntimeError(err)
        return await device.device.async_restart()


def restart_devolo(settings: Settings) -> bool:
    """Reboot the specified devolo device via its management API."""
    return asyncio.run(async_restart_devolo(settings))
