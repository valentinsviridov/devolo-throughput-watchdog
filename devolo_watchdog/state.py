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


def atomic_write_json(path: Path, data: dict) -> None:
    """Atomically replace a JSON file and clean up failed temporary writes."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, encoding="utf-8"
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(data, temporary_file, indent=2)
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        assert temporary_path is not None
        os.replace(temporary_path, path)
    except Exception:
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
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

    def save(self, state: WatchdogState) -> bool:
        """Persist state and report whether the write succeeded."""
        if not self.path:
            return True
        try:
            atomic_write_json(self.path, state.to_dict())
            return True
        except Exception as exc:
            LOG.error("Failed to save state file (%s): %s", self.path, exc)
            return False


def write_heartbeat(path: str | Path, now: float | None = None) -> None:
    """Write current timestamp to heartbeat file for container healthcheck."""
    hb_path = Path(path)
    current_time = time.time() if now is None else now
    try:
        atomic_write_json(hb_path, {"heartbeat": current_time})
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
        age = current_time - hb_time
        return 0 <= age <= max_age_seconds
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return False
