"""Load the devolo device implementation behind one optional-dependency boundary."""

from __future__ import annotations

from importlib import import_module
from typing import Any


def load_device_class() -> Any:
    """Return the installed devolo Device class or raise ImportError."""
    module = import_module("devolo_plc_api")
    device_class = getattr(module, "Device", None)
    if not callable(device_class):
        raise ImportError("Device is unavailable in devolo_plc_api")
    return device_class
