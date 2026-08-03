"""JSON logging configuration shared by all command-line entrypoints."""

from __future__ import annotations

import json
import logging
import time
from datetime import UTC, datetime
from typing import Any


def format_log_timestamp(epoch_seconds: float | None = None) -> str:
    """Return an ISO 8601 UTC timestamp suitable for structured logs."""
    timestamp = time.time() if epoch_seconds is None else epoch_seconds
    return (
        datetime.fromtimestamp(timestamp, tz=UTC)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


class JsonLogFormatter(logging.Formatter):
    """Render each log record as one JSON object.

    Call sites that already provide a JSON object, such as cycle-result logging,
    are merged into the common envelope so their fields remain directly
    queryable. Other messages are stored in the ``message`` field.
    """

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        payload: dict[str, Any]
        try:
            decoded_message = json.loads(message)
        except (json.JSONDecodeError, TypeError):
            decoded_message = None

        if isinstance(decoded_message, dict):
            payload = decoded_message.copy()
        else:
            payload = {"message": message}

        timestamp = payload.pop("timestamp", format_log_timestamp(record.created))
        payload.pop("level", None)
        payload.pop("logger", None)
        formatted_record: dict[str, Any] = {
            "timestamp": timestamp,
            "level": record.levelname,
            "logger": record.name,
            **payload,
        }

        if record.exc_info:
            formatted_record["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            formatted_record["stack"] = self.formatStack(record.stack_info)

        return json.dumps(formatted_record, ensure_ascii=False, default=str)


def configure_json_logging(level: int = logging.INFO) -> None:
    """Configure process-wide logging as one JSON object per line."""
    handler = logging.StreamHandler()
    handler.setFormatter(JsonLogFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)
