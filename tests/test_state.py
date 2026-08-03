from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from devolo_watchdog.models import Status, WatchdogState
from devolo_watchdog.state import StateStore, check_heartbeat, write_heartbeat


class StateStoreTests(unittest.TestCase):
    def test_load_and_save_with_none_path(self):
        store = StateStore(None)
        state = store.load()
        self.assertEqual(state.consecutive_failures, 0)
        # save should execute cleanly without error when path is None
        self.assertTrue(store.save(state))

    def test_load_non_existent_file_returns_fresh_state(self):
        store = StateStore("/tmp/non_existent_watchdog_state.json")
        state = store.load()
        self.assertEqual(state.consecutive_failures, 0)
        self.assertFalse(state.breaker_tripped)

    def test_load_corrupted_json_returns_fresh_state(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write("invalid json content {{{")
            tf.flush()
            path = tf.name

        try:
            store = StateStore(path)
            state = store.load()
            self.assertEqual(state.consecutive_failures, 0)
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_save_and_load_roundtrip(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            path = tf.name

        try:
            store = StateStore(path)
            state = WatchdogState(
                consecutive_failures=3,
                degradation_notification_sent=True,
                breaker_tripped=True,
                last_status=Status.DEGRADED,
                last_reason="Local link slow",
            )
            self.assertTrue(store.save(state))

            loaded = store.load()
            self.assertEqual(loaded.consecutive_failures, 3)
            self.assertTrue(loaded.degradation_notification_sent)
            self.assertTrue(loaded.breaker_tripped)
            self.assertEqual(loaded.last_status, Status.DEGRADED)
            self.assertEqual(loaded.last_reason, "Local link slow")
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @patch("devolo_watchdog.state.atomic_write_json")
    def test_save_exception_logged_gracefully(self, mock_write):
        mock_write.side_effect = PermissionError("Disk full or permission denied")
        store = StateStore("/tmp/some_state.json")
        state = WatchdogState()
        # save should log error and not crash
        self.assertFalse(store.save(state))

    def test_atomic_write_json_cleanup_on_error(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            target_path = Path(tmpdir) / "target.json"
            with patch("json.dump", side_effect=RuntimeError("JSON serialization error")):
                self.assertFalse(StateStore(target_path).save(WatchdogState()))

            self.assertFalse(target_path.exists())
            self.assertEqual(list(Path(tmpdir).iterdir()), [])

    def test_heartbeat_write_and_check(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            path = tf.name

        try:
            now = 1000.0
            write_heartbeat(path, now=now)
            self.assertTrue(check_heartbeat(path, max_age_seconds=30.0, now=now + 10.0))
            self.assertFalse(check_heartbeat(path, max_age_seconds=30.0, now=now + 50.0))
            self.assertFalse(check_heartbeat(path, max_age_seconds=30.0, now=now - 1.0))
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_check_heartbeat_missing_file(self):
        self.assertFalse(check_heartbeat("/tmp/non_existent_hb.json"))

    def test_check_heartbeat_corrupted_file(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            tf.write("not json content")
            tf.flush()
            path = tf.name

        try:
            self.assertFalse(check_heartbeat(path))
        finally:
            if os.path.exists(path):
                os.unlink(path)

    @patch("devolo_watchdog.state.atomic_write_json")
    def test_write_heartbeat_exception_logged_gracefully(self, mock_write):
        mock_write.side_effect = OSError("Disk write error")
        # Should log warning without raising
        write_heartbeat("/tmp/hb.json")
