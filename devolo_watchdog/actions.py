"""Hardware actions and device recovery integration."""

from __future__ import annotations

from pathlib import Path

from devolo_watchdog.config import Settings


def read_password(path: str | None) -> str | None:
    """Read password from file if path is specified."""
    if not path:
        return None
    try:
        return Path(path).read_text(encoding="utf-8").strip() or None
    except (FileNotFoundError, PermissionError) as exc:
        raise ValueError(f"DW_PASSWORD_FILE unreadable ({path}): {exc}") from None


def restart_devolo(settings: Settings) -> bool:
    """Reboot the specified devolo device via its management API."""
    from devolo_plc_api import Device

    with Device(ip=settings.devolo_ip) as device:
        if password := read_password(settings.password_file):
            device.password = password
        return bool(device.device.restart())
