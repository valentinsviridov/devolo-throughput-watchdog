"""Atomic file-backed state storage and heartbeat management."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import time
from pathlib import Path

from devolo_watchdog.models import WatchdogState

LOG = logging.getLogger("devolo-throughput-watchdog")


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tf = tempfile.NamedTemporaryFile("w", dir=path.parent, delete=False, encoding="utf-8")
    try:
        json.dump(data, tf, indent=2)
        tf.flush()
        os.fsync(tf.fileno())
        tf.close()
        os.replace(tf.name, path)
    except Exception:
        if os.path.exists(tf.name):
            try:
                os.unlink(tf.name)
            except OSError:
                pass
        raise


class StateStore:
    """Manages persistent WatchdogState in a JSON file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else None

    def load(self) -> WatchdogState:
        if not self.path or not self.path.exists():
            return WatchdogState()
        try:
            content = self.path.read_text(encoding="utf-8")
            data = json.loads(content)
            return WatchdogState.from_dict(data)
        except Exception as exc:
            LOG.warning("Failed to load state file (%s): %s. Starting fresh state.", self.path, exc)
            return WatchdogState()

    def save(self, state: WatchdogState) -> None:
        if not self.path:
            return
        try:
            _atomic_write_json(self.path, state.to_dict())
        except Exception as exc:
            LOG.error("Failed to save state file (%s): %s", self.path, exc)


def write_heartbeat(path: str | Path, now: float | None = None) -> None:
    """Write current timestamp to heartbeat file for container healthcheck."""
    hb_path = Path(path)
    current_time = time.time() if now is None else now
    try:
        _atomic_write_json(hb_path, {"heartbeat": current_time})
    except Exception as exc:
        LOG.warning("Failed to write heartbeat file (%s): %s", hb_path, exc)


def check_heartbeat(
    path: str | Path, max_age_seconds: float = 60.0, now: float | None = None
) -> bool:
    """Check if heartbeat file exists and was updated within max_age_seconds."""
    hb_path = Path(path)
    if not hb_path.exists():
        return False
    current_time = time.time() if now is None else now
    try:
        data = json.loads(hb_path.read_text(encoding="utf-8"))
        hb_time = float(data.get("heartbeat", 0))
        return (current_time - hb_time) <= max_age_seconds
    except Exception:
        return False
