from __future__ import annotations

import io
import json
import logging
import unittest
from unittest.mock import patch

from devolo_watchdog.logging_config import JsonLogFormatter, configure_json_logging


class JsonLogFormatterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.stream = io.StringIO()
        self.handler = logging.StreamHandler(self.stream)
        self.handler.setFormatter(JsonLogFormatter())
        self.logger = logging.getLogger(f"{__name__}.{self.id()}")
        self.logger.handlers = [self.handler]
        self.logger.setLevel(logging.DEBUG)
        self.logger.propagate = False

    def tearDown(self) -> None:
        self.logger.handlers.clear()

    def payload(self) -> dict:
        rendered = self.stream.getvalue()
        self.assertEqual(len(rendered.splitlines()), 1)
        return json.loads(rendered)

    def test_plain_log_record_is_json(self) -> None:
        self.logger.warning("probe failed: %s", "timeout")

        payload = self.payload()
        self.assertEqual(payload["level"], "WARNING")
        self.assertEqual(payload["logger"], self.logger.name)
        self.assertEqual(payload["message"], "probe failed: timeout")
        self.assertTrue(payload["timestamp"].endswith("Z"))

    def test_structured_message_is_merged_into_envelope(self) -> None:
        self.logger.info(json.dumps({"event": "watchdog_started", "mode": "daemon"}))

        payload = self.payload()
        self.assertEqual(payload["event"], "watchdog_started")
        self.assertEqual(payload["mode"], "daemon")
        self.assertNotIn("message", payload)

    def test_exception_is_escaped_in_single_json_line(self) -> None:
        try:
            raise RuntimeError("device unavailable")
        except RuntimeError:
            self.logger.exception("reboot failed")

        payload = self.payload()
        self.assertEqual(payload["message"], "reboot failed")
        self.assertIn("RuntimeError: device unavailable", payload["exception"])

    @patch("devolo_watchdog.logging_config.logging.basicConfig")
    def test_process_logging_uses_json_formatter(self, mock_basic_config) -> None:
        configure_json_logging()

        handler = mock_basic_config.call_args.kwargs["handlers"][0]
        self.assertIsInstance(handler.formatter, JsonLogFormatter)
        self.assertTrue(mock_basic_config.call_args.kwargs["force"])
