from __future__ import annotations

import os
import tempfile
import unittest

from devolo_watchdog.models import Status, WatchdogState
from devolo_watchdog.state import StateStore, check_heartbeat, write_heartbeat


class StateStoreTests(unittest.TestCase):
    def test_load_non_existent_file_returns_fresh_state(self):
        store = StateStore("/tmp/non_existent_watchdog_state.json")
        state = store.load()
        self.assertEqual(state.consecutive_failures, 0)
        self.assertFalse(state.breaker_tripped)

    def test_save_and_load_roundtrip(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            path = tf.name

        try:
            store = StateStore(path)
            state = WatchdogState(
                consecutive_failures=3,
                breaker_tripped=True,
                last_status=Status.DEGRADED,
                last_reason="Local link slow",
            )
            store.save(state)

            loaded = store.load()
            self.assertEqual(loaded.consecutive_failures, 3)
            self.assertTrue(loaded.breaker_tripped)
            self.assertEqual(loaded.last_status, Status.DEGRADED)
            self.assertEqual(loaded.last_reason, "Local link slow")
        finally:
            if os.path.exists(path):
                os.unlink(path)

    def test_heartbeat_write_and_check(self):
        with tempfile.NamedTemporaryFile("w+", delete=False) as tf:
            path = tf.name

        try:
            now = 1000.0
            write_heartbeat(path, now=now)
            self.assertTrue(check_heartbeat(path, max_age_seconds=30.0, now=now + 10.0))
            self.assertFalse(check_heartbeat(path, max_age_seconds=30.0, now=now + 50.0))
        finally:
            if os.path.exists(path):
                os.unlink(path)
